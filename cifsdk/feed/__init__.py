from cifsdk.feed.fqdn import Fqdn
from cifsdk.feed.ipv4 import Ipv4
from cifsdk.feed.ipv6 import Ipv6
from cifsdk.feed.url import Url

plugins = {
    'ipv4': Ipv4,
    'ipv6': Ipv6,
    'fqdn': Fqdn,
    'url': Url
}


# http://stackoverflow.com/a/456747
def factory(name):
    if name in plugins:
        return plugins[name]
    else:
        return None


def tag_contains_whitelist(data):
    for d in data:
        if d == 'whitelist':
            return True
