import json,sys,re,urllib.request,xml.etree.ElementTree as ET,time
UA={"User-Agent":"research-bot research@example.com"}
def get(u):
    time.sleep(0.2)
    return urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=30).read()
out={}
for cik in sys.argv[2:]:
    c=cik.zfill(10)
    try:
        sub=json.loads(get(f"https://data.sec.gov/submissions/CIK{c}.json"))
        r=sub["filings"]["recent"]
        idx=[i for i,f in enumerate(r["form"]) if f=="13F-HR"]
        if not idx: out[c]={"name":sub["name"],"error":"no 13F-HR"};continue
        i=idx[0]; acc=r["accessionNumber"][i]; nd=acc.replace("-","")
        ix=json.loads(get(f"https://www.sec.gov/Archives/edgar/data/{int(c)}/{nd}/index.json"))
        names=[x["name"] for x in ix["directory"]["item"] if x["name"].lower().endswith(".xml") and "primary" not in x["name"].lower()]
        tbl=[n for n in names if "info" in n.lower() or "table" in n.lower()] or names
        x=get(f"https://www.sec.gov/Archives/edgar/data/{int(c)}/{nd}/{tbl[0]}").decode("utf8","ignore")
        x=re.sub(r'\sxmlns(:\w+)?="[^"]+"',"",x); x=re.sub(r"<(/?)\w+:",r"<\1",x); x=re.sub(r"\s\w+:\w+=\"[^\"]*\"","",x)
        root=ET.fromstring(x); agg={}
        for it in root.iter("infoTable"):
            n=(it.findtext("nameOfIssuer") or "").strip(); v=int(it.findtext("value") or 0)
            agg[n]=agg.get(n,0)+v
        tot=sum(agg.values())
        top=sorted(agg.items(),key=lambda kv:-kv[1])[:25]
        out[c]={"name":sub["name"],"period":r["reportDate"][i],"filed":r["filingDate"][i],"accession":acc,"positions":len(agg),"total_value":tot,"top":top}
    except Exception as e: out[c]={"error":repr(e)}
json.dump(out,open(sys.argv[1],"w"),indent=1)
for c,d in out.items(): print(c,d.get("name"),d.get("period"),d.get("positions"),d.get("error",""))
