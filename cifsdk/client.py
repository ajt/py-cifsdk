import json
import time
import sys
import os
import os.path
import yaml
import logging
import select
from cifsdk.format import factory as format_factory
from cifsdk.feed import factory as feed_factory

from argparse import ArgumentParser
from argparse import RawDescriptionHelpFormatter
import textwrap
import copy
import arrow

from cifsdk import VERSION, API_VERSION
from cifsdk.constants import REMOTE_ADDR, LIMIT, FEED_CONFIDENCE, WHITELIST_LIMIT, PROXY, FEED_LIMIT, TOKEN

# https://urllib3.readthedocs.org/en/latest/security.html#disabling-warnings
# http://stackoverflow.com/questions/14789631/hide-userwarning-from-urllib2
import requests
requests.packages.urllib3.disable_warnings()

from cifsdk.utils import setup_logging, read_config


class Client(object):

    def __init__(self, token, remote=REMOTE_ADDR, proxy=None, timeout=300, verify_ssl=True, nowait=False):
        """
        Initiates a client object

        :param token: <cif token> (ex: 6e10366ce0a25227aac810b4058c3712d30d3848f4d5d8f586658178a65c67df)
        :param remote: server location (ex: https://localhost)
        :param proxy: proxy server location
        :param timeout: seconds for client timeout (default: 300)
        :param no_verify_ssl: turn off TLS verification (default: False)
        :param nowait: batch submissions on the server, do not wait for returned submission id's. Best to
               to use this if submitting more than 100 records at a time. (default: False)
        :return: object
        """
        
        self.logger = logging.getLogger(__name__)
        self.remote = remote
        self.token = str(token)
        self.proxy = proxy
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        
        self.session = requests.session()
        self.session.headers["Accept"] = "application/vnd.cif.v{0}+json".format(API_VERSION)
        self.session.headers['User-Agent'] = "py-cifsdk/{0}".format(VERSION)
        self.session.headers['Authorization'] = "Token token={0}".format(self.token)
        self.session.headers['Content-Type'] = 'application/json'

        self.nowait = nowait
    
    def search(self, query=None, filters={}, limit=None, nolog=None, sort='lasttime', decode=True):
        """returns search result set based on either query or filters

        :param query: a single observable (ex: example.com, 192.168.1.1, ...)
        :param filters: filter results by various attributes: https://github.com/csirtgadgets/massive-octo-spice/wiki/API
        :param limit: limit return results
        :param nolog: do NOT log query
        :param sort: sort result set (default: 'lasttime')
        :param decode: decode the results from JSON (default: yes)
        :return: list of dicts (observables)
        """
        filters['limit'] = limit
        filters['nolog'] = nolog

        if query:
            filters['observable'] = query
        
        uri = self.remote + '/observables'

        if filters.get('tags') and type(filters.get('tags')) is list:
            filters['tags'] = ','.join(filters['tags'])
            
        self.logger.debug('uri: %s' % uri)
        self.logger.debug('params: %s', json.dumps(filters))

        self.logger.info('searching...')

        body = self.session.get(uri, params=filters, verify=self.verify_ssl)

        self.logger.debug('status code: ' + str(body.status_code))

        if body.status_code > 299:
            self.logger.warning('request failed: %s' % str(body.status_code))
            raise SystemExit

        ret = body.content

        if decode:
            self.logger.info('decoding...')
            ret = json.loads(ret)

            self.logger.info('sorting...')
            ret = sorted(ret, key=lambda o: o[sort])

        self.logger.debug('returning..')
        return ret

    def submit(self, data):
        """
        Submit records to CIF

        :param data: a single dict or a list of dicts
        :return: list

        list
        [{"observable": "1.1.1.1", "confidence": "85", "tlp": "amber", "group": "everyone", "tags": ["zeus","botnet"],
        "provider": "me.com"}, {"observable": "1.1.1.1", "confidence": "85", "tlp": "amber", "group": "everyone",
        "tags": "malware", "provider": "me.com"}]
        """

        if type(data) == dict:
            data = [data]

        if type(data[0]) != dict:
            raise RuntimeError('submitted data must be a dictionary')

        data = json.dumps(data)

        # TODO - http://docs.python-requests.org/en/latest/user/quickstart/#more-complicated-post-requests
        uri = self.remote + '/observables'

        if self.nowait:
            uri = "{0}?nowait=1".format(uri)
        
        self.logger.debug('uri: %s' % uri)

        body = self.session.post(uri, data=data, verify=self.verify_ssl)
        self.logger.debug('status code: ' + str(body.status_code))
        if body.status_code > 299:
            self.logger.error('request failed: %s' % str(body.status_code))
            self.logger.error(json.loads(body.text).get('message'))
            return None
        
        body = json.loads(body.text)
        return body
    
    def ping(self):
        """
        Ping the server to verify connectivity
        :return: str
        """
        t0 = time.time()
        uri = str(self.remote) + '/ping'
        body = self.session.get(uri, params={}, verify=self.verify_ssl)
        
        self.logger.debug('status code: ' + str(body.status_code))
        if body.status_code > 299:
            self.logger.error('request failed: %s' % str(body.status_code))
            return 'request failed: %s' % str(body.status_code)
        
        t1 = (time.time() - t0)
        self.logger.debug('return time: %.15f' % t1)
        return t1

    def aggregate(self, data, field='observable', sort='confidence'):
        """
        aggregate data
        :param data:
        :param field:
        :param sort:
        :return:
        """
        x = set()
        rv = []
        for d in sorted(data, key=lambda x: x[sort], reverse=True):
            if d[field] not in x:
                x.add(d[field])
                rv.append(d)

        return rv


def main():

    p = ArgumentParser(
        description=textwrap.dedent('''\
        example usage:
            $ cif --query example.com
            $ cif -q 1.2.3.0/24 --feed --format csv
        '''),
        formatter_class=RawDescriptionHelpFormatter,
        prog='cif'
    )

    # options
    p.add_argument("-v", "--verbose", dest="verbose", action="store_true", help="logging level: INFO")
    p.add_argument('-d', '--debug', dest='debug', action="store_true", help="logging level: DEBUG")
    p.add_argument('-V', '--version', action='version', version=VERSION)
    p.add_argument('--no-verify-ssl', action="store_true", default=False)
    p.add_argument('-R', '--remote',  help="remote api location")
    p.add_argument('-T', '--token', help="specify token",  default=TOKEN)
    p.add_argument('--timeout',  help='connection timeout [default: %(default)s]', default="300")
    p.add_argument('-C', '--config',  help="configuration file [default: %(default)s]",
                   default=os.path.expanduser("~/.cif.yml"))

    p.add_argument('--sort', help='sort output ASC by key', default='reporttime')
    p.add_argument('-f', '--format', help="specify output format [default: %(default)s]", default="table")

    # actions
    p.add_argument('-p', '--ping', action="store_true", help="ping")
    p.add_argument('-s', '--submit', action="store_true", help="submit a JSON object")

    # flags
    p.add_argument('-l', '--limit', help="result limit")
    p.add_argument('-n', '--nolog', help='do not log the search', default=None, action="store_true")

    # filters
    p.add_argument('-q', "--query", help="specify a search")
    p.add_argument('--firsttime', help='specify filter based on firsttime timestmap (greater than, '
                                       'format: YYYY-MM-DDTHH:MM:SSZ)')
    p.add_argument('--lasttime', help='specify filter based on lasttime timestamp (less than, format: '
                                      'YYYY-MM-DDTHH:MM:SSZ)')
    p.add_argument('--reporttime', help='specify filter based on reporttime timestmap (greater than, format: '
                                        'YYYY-MM-DDTHH:MM:SSZ)')
    p.add_argument('--reporttimeend', help='specify filter based on reporttime timestmap (less than, format: '
                                           'YYYY-MM-DDTHH:MM:SSZ)')
    p.add_argument("--tags", help="filter for tags")
    p.add_argument('--description', help='filter on description')
    p.add_argument('--otype', help='filter by otype')
    p.add_argument("--cc", help="filter for countrycode")
    p.add_argument('-c', '--confidence', help="specify confidence")
    p.add_argument('--rdata', help='filter by rdata')
    p.add_argument('--provider', help='filter by provider')
    p.add_argument('--asn', help='filter by asn')
    p.add_argument('--proxy', help="specify a proxy to use [default %(default)s]", default=PROXY)

    p.add_argument('--feed', action="store_true", help="generate a feed of data, meaning deduplicated and whitelisted")
    p.add_argument('--whitelist-limit', help="specify how many whitelist results to use when applying to --feeds "
                                             "[default %(default)s]", default=WHITELIST_LIMIT)
    p.add_argument('--last-day', action="store_true", help='auto-sets reporttime to 23 hours and 59 seconds ago '
                                                           '(current time UTC) and reporttime-end to "now"')
    p.add_argument('--days', help='filter results within last X days')

    p.add_argument('--aggregate', help="aggregate around a specific field (ie: observable)")

    # Process arguments
    args = p.parse_args()
    setup_logging(args)
    logger = logging.getLogger(__name__)

    o = read_config(args)
    options = vars(args)
    for v in options:
        if options[v] is None:
            options[v] = o.get(v)

    if not options.get('token'):
        raise RuntimeError('missing --token')

    verify_ssl = True
    if o.get('no_verify_ssl') or options.get('no_verify_ssl'):
        verify_ssl = False

    cli = Client(options['token'], remote=options['remote'], proxy=options.get('proxy'), verify_ssl=verify_ssl)

    try:
        if(options.get('query') or options.get('tags') or options.get('cc') or options.get('rdata') or options.get(
                'otype') or options.get('provider') or options.get('asn') or options.get('description')):
            filters = {}
            if options.get('query'):
                filters['observable'] = options['query']
            if options.get('cc'):
                filters['cc'] = options['cc']

            if options.get('tags'):
                filters['tags'] = options['tags']

            if options.get('description'):
                filters['description'] = options['description']

            if options.get('confidence'):
                filters['confidence'] = options['confidence']
            else:
                if options.get('feed'):
                    filters['confidence'] = FEED_CONFIDENCE

            if options.get('firsttime'):
                filters['firsttime'] = options['firsttime']

            if options.get('lasttime'):
                filters['lasttime'] = options['lasttime']

            if options.get('reporttime'):
                filters['reporttime'] = options['reporttime']

            if options.get('reporttimeend'):
                filters['reporttimeend'] = options['reporttimeend']

            if options.get('otype'):
                filters['otype'] = options['otype']

            if options.get('rdata'):
                filters['rdata'] = options['rdata']

            if options.get('nolog'):
                options['nolog'] = 1

            if options.get('provider'):
                filters['provider'] = options['provider']

            if options.get('asn'):
                filters['asn'] = options['asn']

            if options.get('last_day'):
                now = arrow.utcnow()
                filters['reporttimeend'] = '{}Z'.format(now.format('YYYY-MM-DDTHH:mm:ss'))
                now = now.replace(days=-1)
                filters['reporttime'] = '{}Z'.format(now.format('YYYY-MM-DDTHH:mm:ss'))

            if options.get('days'):
                now = arrow.utcnow()
                filters['reporttimeend'] = '{}Z'.format(now.format('YYYY-MM-DDTHH:mm:ss'))
                now = now.replace(days=-int(options['days']))
                filters['reporttime'] = '{}Z'.format(now.format('YYYY-MM-DDTHH:mm:ss'))

            mylimit = options.get('limit', LIMIT)
            if options.get('feed'):
                limit = FEED_LIMIT

            ret = cli.search(limit=mylimit, nolog=options['nolog'], filters=filters, sort=options.get('sort'))

            if options.get('aggregate'):
                ret = cli.aggregate(ret, field=options['aggregate'])

            if options.get('feed'):
                wl_filters = copy.deepcopy(filters)
                wl_filters['tags'] = 'whitelist'

                wl = cli.search(limit=options['whitelist_limit'], nolog=True, filters=wl_filters)

                f = feed_factory(options['otype'])
                ret = cli.aggregate(ret)

                ret = f().process(ret, wl)

            f = format_factory(options['format'])

            try:
                if len(ret) >= 1:
                    print(f(ret))
                else:
                    logger.info("no results found...")
            except AttributeError as e:
                logger.exception(e)

        elif options.get('ping'):
            for num in range(0,4):
                ret = cli.ping()
                print("roundtrip: %s ms" % ret)
                select.select([], [], [], 1)
        elif options.get('submit'):

            if not sys.stdin.isatty():
                stdin = sys.stdin.read()
            else:
                logger.error("No data passed via STDIN")
                raise SystemExit

            try:
                data = json.loads(stdin)
                try:
                    ret = cli.submit(data)
                    print('submitted: {0}'.format(ret))
                except Exception as e:
                    logger.error(e)
                    raise SystemExit
            except Exception as e:
                logger.error(e)
                raise SystemExit
        else:
            logger.warning('operation not supported')
            p.print_help()
            raise SystemExit

    except KeyboardInterrupt:
        raise SystemExit
    except Exception as e:
        logger.error(e)
        raise SystemExit

if __name__ == "__main__":
    main()
