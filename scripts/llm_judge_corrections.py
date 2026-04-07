"""LLM-as-judge 결과로 golden dataset 라벨 수정.

각 아티클의 title + description + body preview를 기반으로
Claude Opus가 판단한 category, quality_score, keywords를 적용.

판단 기준:
- Category: 13개 카테고리 중 가장 적합한 것 (원래 분류가 맞으면 유지)
- Quality: 5=Deep technical, 4=Solid technical, 3=Introductory, 2=Non-technical, 1=Unusable/spam
- Keywords: 아티클 내용에서 핵심 3개
"""
import json
import os

# (index, corrected_category, corrected_quality, corrected_keywords)
# Category: 1=Frontend, 2=Backend, 3=Mobile, 4=AI/ML, 5=Database,
#           6=Security/Network, 7=Design, 8=PM, 9=DevOps/Infra,
#           10=HW/IoT, 11=QA/Test, 12=Culture, 13=ETC

CORRECTIONS = {
    0:  (1,  5, "frontend\ttrivago\tengineering practices"),
    1:  (4,  4, "MCP\tAI agent\tpayment API"),
    2:  (9,  4, "Cloudflare Workers\tdev environment\tedge computing"),
    3:  (4,  4, "AI concierge\tmulti-agent\tAWS Bedrock"),
    4:  (4,  3, "LY Corporation\tAI\tTech-Verse"),
    5:  (2,  5, "search engine\tDropbox\tfull-text search"),
    6:  (4,  5, "vector search\tretrieval\te-commerce search"),
    7:  (13, 3, "Cyber Monday\te-commerce\tonline shopping"),
    8:  (2,  4, "language migration\tbackend\tKotlin"),
    9:  (2,  5, "STOMP\tnon-blocking\tJava"),
    10: (2,  5, "distributed systems\toutbox pattern\teventual consistency"),
    11: (12, 3, "junior developer\tcareer growth\tcode review"),
    12: (12, 2, "hackathon\tDropbox\thack night"),
    13: (2,  4, "bulk processing\tbatch insert\tSpring Batch"),
    14: (2,  4, "coding convention\tbackend\tcode quality"),
    15: (8,  2, "Dropbox Business\tproductivity\tcollaboration"),
    16: (2,  3, "API deprecation\tmigration\tDropbox API"),
    17: (12, 2, "hackathon\tHackZurich\tDropbox"),
    18: (3,  5, "mobile tracking\tAutoTrack SDK\ttroubleshooting"),
    19: (3,  5, "cross-platform\tiOS\tAndroid"),
    20: (3,  3, "file permissions\tDropbox API\tmobile"),
    21: (3,  3, "SwiftyDropbox\tSwift\tiOS SDK"),
    22: (3,  5, "Spotify\tmobile release\tCI/CD"),
    23: (6,  3, "OAuth\tauthentication\tDropbox API"),
    24: (4,  5, "recommendation\tA/B testing\tML"),
    25: (4,  4, "MLOps\trecommendation\tcontainer"),
    26: (8,  2, "Acompli\temail\tdocument preview"),
    27: (4,  5, "LLM\tpost-training\tKanana-2"),
    28: (4,  4, "machine learning\tprediction\tDropbox"),
    29: (2,  4, "performance\tthumbnails\toptimization"),
    30: (6,  3, "bug bounty\tsecurity\tDropbox"),
    31: (4,  4, "AWS Bedrock\tprompt caching\tLLM optimization"),
    32: (9,  5, "post-mortem\toutage\tincident"),
    33: (4,  4, "ADK\tAI agents\tGoogle"),
    34: (2,  3, "Datastore API\tcross-platform\topen source"),
    35: (4,  3, "NAVER Place\tAI\tteam introduction"),
    36: (4,  4, "Bedrock Guardrails\tresponsible AI\tcontext-aware"),
    37: (4,  1, "N/A\tN/A\tN/A"),  # blog index page
    38: (4,  5, "foundation model\tdistributed training\tSageMaker"),
    39: (10, 3, "NVIDIA IGX Thor\tedge AI\tindustrial"),
    40: (5,  4, "database migration\tAmazon RDS\tSQL Server"),
    41: (9,  5, "eBPF\tPrometheus\tmonitoring"),
    42: (9,  4, "jemalloc\tmemory allocator\tMeta"),
    43: (5,  3, "SimpleDB\tS3\tdata export"),
    44: (5,  4, "RDS\tbackup automation\tDST"),
    45: (5,  5, "Redis\tlarge-scale\tDBA"),
    46: (2,  4, "insurance\tinstant payment\tfintech"),
    47: (5,  4, "Workers KV\tkey-value store\tCloudflare"),
    48: (6,  3, "Magic Transit\tDDoS\tnetwork protection"),
    49: (6,  5, "Linux Crypto API\tcryptography\tkernel"),
    50: (6,  1, "N/A\tN/A\tN/A"),  # blog index page
    51: (6,  3, "IPv6\tCloudflare\tWorld IPv6 Day"),
    52: (6,  3, "phishing\tCOVID-19\tsecurity"),
    53: (6,  5, "IP fragmentation\tpackets\tMTU"),
    54: (6,  4, "DDoS\tmitigation\tCloudflare"),
    55: (6,  2, "acquisition\tStopTheHacker\tanti-malware"),
    56: (13, 2, "Railgun\te-commerce\tperformance"),
    57: (9,  4, "edge network\tDropbox\tinfrastructure"),
    58: (9,  5, "sovereignty\tfailover\tAWS"),
    59: (9,  4, "EC2 R8a\tAMD EPYC\tbenchmark"),
    60: (13, 1, "customer story\tCloudflare\ttraffic"),
    61: (9,  4, "GitHub\tavailability\tincident report"),
    62: (9,  4, "Cloudflare Tunnel\tquick tunnel\tnetworking"),
    63: (6,  5, "TAP devices\tvirtual networking\tFirecracker"),
    64: (9,  4, "edge logging\tWorkers\tobservability"),
    65: (9,  3, "data center\tglobal network\tupgrade"),
    66: (9,  5, "deploy safety\treliability\tSlack"),
    67: (9,  4, "GitHub Actions\tCI/CD\tTerraform"),
    68: (9,  5, "incident report\tSpotify\tEnvoy Proxy"),
    69: (9,  4, "event-driven\tmessaging system\tDropbox"),
    70: (9,  5, "live streaming\tNetflix\tinfrastructure"),
    71: (9,  4, "configuration\tglobal distribution\tCloudflare"),
    72: (1,  5, "micro frontends\tdeployment\tDelivery Hero"),
    73: (9,  4, "AWS CDK\tTerraform\tIaC"),
    74: (9,  5, "Kubernetes\tstreaming pipeline\tLINE"),
    75: (12, 2, "AWS CDK\tcommunity\topen source"),
    76: (9,  4, "SLI\tSLO\tSRE"),
    77: (9,  5, "Prometheus\tmonitoring\tKubernetes"),
    78: (9,  2, "data center\tPortland\tCloudflare"),
    79: (9,  4, "cloud migration\tGCP\timages"),
    80: (12, 3, "career\tIT industry\tgrowth"),
    81: (11, 4, "Playwright\tflaky test\ttest automation"),
    82: (11, 4, "mobile testing\tautomation\tmonitoring"),
    83: (12, 3, "developer culture\tinternal tools\tknowledge sharing"),
    84: (11, 5, "testing\tsync engine\tDropbox"),
    85: (11, 4, "virtual device\tmobile testing\tmultiverse"),
    86: (6,  5, "LLM\tvulnerability analysis\tsecurity automation"),
    87: (6,  4, "network troubleshooting\tglobal service\tSRE"),
    88: (12, 3, "diversity\tinclusion\tworkforce"),
    89: (8,  3, "product management\tcollaboration\tDelivery Hero"),
    90: (8,  3, "quick commerce\tSSG\tproduct launch"),
    91: (12, 3, "conference\tdeveloper relations\tkakaopay"),
    92: (13, 2, "Basekit\tbusiness\tgrowth"),
    93: (12, 2, "mentorship\twomen in tech\tdiversity"),
    94: (12, 1, "internship\trecruitment\tSK Planet"),
    95: (12, 2, "diversity\trepresentation\ttech career"),
    96: (13, 3, "internet trends\tholidays\ttraffic"),
    97: (13, 1, "mince pie\tCloudflare\tfun"),
    98: (12, 2, "Greencloud\tsustainability\tclimate"),
    99: (13, 3, "internet regulation\tSupreme Court\tfree speech"),
    100:(1,  3, "Dropbox Extensions\timage flipping\tSDK"),
    101:(13, 1, "Hurricane Harvey\tdisaster\tcharity"),
    102:(12, 2, "LGBTQ+\tProudflare\tdiversity"),
    103:(12, 1, "meetup\tcommunity management\tsocial media"),
    # Rejected articles (104-118)
    104:(13, 2, "FlowVella\tDropbox integration\tpresentation"),
    105:(3,  1, "Xamarin\twebinar\tDropbox"),
    106:(12, 2, "developer program\tstartup\tDropbox"),
    107:(12, 2, "DBX\tdeveloper conference\tDropbox"),
    108:(12, 2, "hackathon\tVideo Hack Day\tDropbox"),
    109:(13, 2, "Dropbox Chooser\tintegration\tapps"),
    110:(12, 2, "hackathon\tHackZurich\tDropbox"),
    111:(12, 2, "hackathon\tHack the North\tDropbox"),
    112:(13, 2, "Dropbox Chooser\tcustomer support\tinvoicing"),
    113:(6,  3, "bug bounty\tsecurity\tDropbox"),
    114:(12, 1, "developer events\tDropbox\tNovember"),
    115:(2,  3, "Dropbox API\tdelta\tpath filtering"),
    116:(3,  2, "Cordova\tDropbox datastores\tmobile"),
    117:(1,  3, "JavaScript\tguild summit\tfrontend"),
    118:(12, 2, "meetup\tdeveloper events\tDropbox"),
}


def main():
    input_path = os.path.join(os.path.dirname(__file__), "..", "data", "golden_seed.jsonl")
    output_path = input_path  # overwrite

    with open(input_path, "r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    changed = 0
    for i, row in enumerate(rows):
        if i not in CORRECTIONS:
            continue
        cat, quality, keywords = CORRECTIONS[i]
        old_cat = row.get("human_category")
        old_q = row.get("human_quality")

        row["human_category"] = cat
        row["human_quality"] = quality
        row["human_keywords"] = keywords

        if old_cat != cat or old_q != quality:
            changed += 1

    with open(output_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"[DONE] {changed}/{len(rows)} entries corrected")

    # 통계
    from collections import Counter
    cat_dist = Counter(CORRECTIONS[i][0] for i in CORRECTIONS)
    q_dist = Counter(CORRECTIONS[i][1] for i in CORRECTIONS)
    print(f"\nCategory distribution: {dict(sorted(cat_dist.items()))}")
    print(f"Quality distribution: {dict(sorted(q_dist.items()))}")


if __name__ == "__main__":
    main()
