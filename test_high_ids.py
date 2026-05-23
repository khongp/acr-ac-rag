import urllib.request
for i in [500, 600, 700, 800, 900, 1000, 2000, 3000]:
    try:
        r = urllib.request.urlopen(f'https://gravitas.acr.org/ACPortal/TopicNarrativePdf?topicId={i}')
        print(i, r.getcode())
    except Exception as e:
        print(i, e)
