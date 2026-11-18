import os
import re
import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# 사용자 관심 기술 스택 키워드 (정규식 단어 경계 매칭용)
STACK_KEYWORDS = {
    # OS / System / Core
    "linux": "Linux",
    "kernel": "Linux Kernel",
    "ubuntu": "Ubuntu",
    "debian": "Debian",
    "redhat": "RHEL",
    "rhel": "RHEL",
    "rocky": "Rocky Linux",
    "centos": "CentOS",
    "almalinux": "AlmaLinux",
    "openssh": "OpenSSH",
    "openssl": "OpenSSL",
    "sudo": "Sudo",
    "glibc": "glibc",
    "systemd": "systemd",
    "bash": "Bash",
    "curl": "cURL",
    # Web / APM
    "apache": "Apache",
    "httpd": "Apache httpd",
    "nginx": "Nginx",
    "tomcat": "Tomcat",
    "php": "PHP",
    "wordpress": "WordPress",
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "nextjs": "Next.js",
    "next.js": "Next.js",
    "spring": "Spring",
    # Database
    "mysql": "MySQL",
    "mariadb": "MariaDB",
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "redis": "Redis",
    "mongodb": "MongoDB",
    "sqlite": "SQLite",
    "mssql": "MSSQL",
    # Cloud / Container / Infra
    "aws": "AWS",
    "amazon": "AWS/Amazon",
    "docker": "Docker",
    "containerd": "containerd",
    "runc": "runc",
    "kubernetes": "Kubernetes",
    "k8s": "Kubernetes",
    "terraform": "Terraform",
    "ansible": "Ansible",
    "github": "GitHub",
    "gitlab": "GitLab",
    "jenkins": "Jenkins",
    "grafana": "Grafana",
    "prometheus": "Prometheus",
    "vmware": "VMware",
    "esxi": "VMware ESXi",
}

# 단어 경계 정규식 사전 컴파일
_COMPILED_STACK_PATTERNS = [
    (re.compile(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", re.IGNORECASE), label)
    for kw, label in STACK_KEYWORDS.items()
]


def match_stack_keywords(text: str) -> list:
    """텍스트에서 관심 기술 스택 키워드를 추출하여 중복 없는 라벨 리스트로 반환합니다."""
    if not text:
        return []
    matched = []
    seen = set()
    for pattern, label in _COMPILED_STACK_PATTERNS:
        if label not in seen and pattern.search(text):
            seen.add(label)
            matched.append(label)
    return matched


class CVEScraper:
    """
    신규 등록된 CVE(CVE-YYYY-XXXXX) 및 실제 악용 보안취약 사고(CISA KEV)를 수집합니다.
    - 필터 기준:
      1) CISA KEV(실제 악용 확인된 보안 사고) 등록 취약점 전체
      2) CVSS 9.0 이상 (CRITICAL) 치명적 취약점 전체
      3) 내 관심 스택(Linux, Web/APM, DB, AWS, Docker 등) 관련 High(7.0+) 이상 취약점
    """

    def __init__(self):
        self.nvd_api_url = "https://services.nvd.nist.gov/rest/json/cves/2.0"
        self.cisa_kev_url = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
        self.gh_advisories_url = "https://api.github.com/advisories"

    def _http_get_json(self, url: str, headers: dict = None, timeout: int = 12):
        req_headers = {
            "User-Agent": "TelegramSecurityBot/1.0 (CVE Alert Monitor)",
            "Accept": "application/json",
        }
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(url, headers=req_headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)

    def fetch_cisa_kev_recent(self, days: int = 7) -> dict:
        """최근 N일 내 CISA KEV(실제 악용 확인된 취약점 카탈로그)에 등재된 CVE 조회"""
        kev_map = {}
        try:
            data = self._http_get_json(self.cisa_kev_url, timeout=10)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            for vuln in data.get("vulnerabilities", []):
                cve_id = (vuln.get("cveID") or "").strip().upper()
                date_added = vuln.get("dateAdded", "")
                if cve_id.startswith("CVE-") and date_added >= cutoff:
                    vendor = vuln.get("vendorProject", "")
                    product = vuln.get("product", "")
                    short_desc = vuln.get("shortDescription", "")
                    vuln_name = vuln.get("vulnerabilityName", "")
                    combined = f"{vendor} {product} {vuln_name} {short_desc}"
                    kev_map[cve_id] = {
                        "cve_id": cve_id,
                        "published": date_added,
                        "cvss_score": 9.5,  # KEV 등재는 실제 악용 사고이므로 최고 우선순위 부여
                        "severity": "CRITICAL (악용확인)",
                        "description_en": f"[{vendor} {product}] {vuln_name} - {short_desc}".strip(),
                        "matched_stacks": match_stack_keywords(combined),
                        "is_kev": True,
                        "url": f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                        "cve_org_url": f"https://www.cve.org/CVERecord?id={cve_id}",
                    }
        except Exception as e:
            logger.warning(f"[CVEScraper] Failed to fetch CISA KEV feed: {e}")
        return kev_map

    def _extract_nvd_cvss(self, metrics: dict):
        """NVD metrics 객체에서 CVSS 점수(float)와 심각도(str) 추출"""
        for key in ("cvssMetricV31", "cvssMetricV40", "cvssMetricV30", "cvssMetricV2"):
            metric_list = metrics.get(key)
            if metric_list and isinstance(metric_list, list):
                primary = metric_list[0]
                cvss_data = primary.get("cvssData", {})
                score = cvss_data.get("baseScore")
                severity = (
                    cvss_data.get("baseSeverity")
                    or primary.get("baseSeverity")
                    or ""
                ).upper()
                if score is not None:
                    try:
                        score_f = float(score)
                        if not severity:
                            if score_f >= 9.0:
                                severity = "CRITICAL"
                            elif score_f >= 7.0:
                                severity = "HIGH"
                            elif score_f >= 4.0:
                                severity = "MEDIUM"
                            else:
                                severity = "LOW"
                        return score_f, severity
                    except (ValueError, TypeError):
                        pass
        return None, "UNKNOWN"

    def _extract_nvd_cpe_text(self, configurations: list) -> str:
        """NVD CPE 설정에서 벤더/제품명 추출"""
        tokens = []
        for conf in configurations or []:
            for node in conf.get("nodes", []):
                for match in node.get("cpeMatch", []):
                    criteria = match.get("criteria", "")
                    parts = criteria.split(":")
                    if len(parts) >= 5:
                        tokens.append(parts[3])
                        tokens.append(parts[4])
        return " ".join(tokens)

    def fetch_nvd_recent(self, lookback_hours: int = 48) -> list:
        """NVD REST API 2.0에서 최근 등록된 CVE 목록을 가져옵니다."""
        now_utc = datetime.now(timezone.utc)
        start_utc = now_utc - timedelta(hours=lookback_hours)

        params = {
            "pubStartDate": start_utc.strftime("%Y-%m-%dT%H:%M:%S.000"),
            "pubEndDate": now_utc.strftime("%Y-%m-%dT%H:%M:%S.000"),
            "resultsPerPage": "100",
        }
        url = f"{self.nvd_api_url}?{urllib.parse.urlencode(params)}"
        results = []

        try:
            data = self._http_get_json(url, timeout=15)
            for item in data.get("vulnerabilities", []):
                cve = item.get("cve", {})
                cve_id = (cve.get("id") or "").strip().upper()
                if not cve_id.startswith("CVE-"):
                    continue

                vuln_status = (cve.get("vulnStatus") or "").upper()
                if "REJECT" in vuln_status:
                    continue

                descriptions = cve.get("descriptions", [])
                desc_en = ""
                for d in descriptions:
                    if d.get("lang") == "en":
                        desc_en = (d.get("value") or "").strip()
                        break
                if not desc_en and descriptions:
                    desc_en = (descriptions[0].get("value") or "").strip()

                if desc_en.startswith("** REJECT **") or "DO NOT USE THIS CANDIDATE NUMBER" in desc_en:
                    continue

                score, severity = self._extract_nvd_cvss(cve.get("metrics", {}))
                cpe_text = self._extract_nvd_cpe_text(cve.get("configurations", []))
                matched_stacks = match_stack_keywords(f"{desc_en} {cpe_text}")

                published_raw = cve.get("published", "")
                published_date = published_raw[:10] if published_raw else now_utc.strftime("%Y-%m-%d")

                results.append({
                    "cve_id": cve_id,
                    "published": published_date,
                    "cvss_score": score,
                    "severity": severity,
                    "description_en": desc_en,
                    "matched_stacks": matched_stacks,
                    "is_kev": False,
                    "url": f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                    "cve_org_url": f"https://www.cve.org/CVERecord?id={cve_id}",
                })
        except Exception as e:
            logger.warning(f"[CVEScraper] NVD API fetch warning: {e}")

        return results

    def fetch_github_advisories_recent(self, per_page: int = 40) -> list:
        """GitHub Security Advisories API에서 최신 CVE를 보조 수집합니다 (NVD 지연/장애 대비)."""
        results = []
        headers = {"X-GitHub-Api-Version": "2022-11-28"}
        gh_token = os.getenv("GITHUB_TOKEN")
        if gh_token:
            headers["Authorization"] = f"Bearer {gh_token}"

        url = f"{self.gh_advisories_url}?type=reviewed&per_page={per_page}"
        try:
            items = self._http_get_json(url, headers=headers, timeout=12)
            if not isinstance(items, list):
                return results

            for adv in items:
                cve_id = (adv.get("cve_id") or "").strip().upper()
                if not cve_id.startswith("CVE-"):
                    continue

                severity = (adv.get("severity") or "UNKNOWN").upper()
                cvss_obj = adv.get("cvss") or {}
                score = cvss_obj.get("score")
                try:
                    score = float(score) if score is not None else None
                except (ValueError, TypeError):
                    score = None

                if score is None:
                    if severity == "CRITICAL":
                        score = 9.0
                    elif severity == "HIGH":
                        score = 7.5

                summary = (adv.get("summary") or "").strip()
                desc = (adv.get("description") or "").strip()
                first_line_desc = desc.split("\n")[0].strip() if desc else ""
                combined_desc = f"{summary}. {first_line_desc}".strip(". ")

                pkg_names = []
                for v in adv.get("vulnerabilities") or []:
                    pkg = (v.get("package") or {}).get("name")
                    eco = (v.get("package") or {}).get("ecosystem")
                    if pkg:
                        pkg_names.append(pkg)
                    if eco:
                        pkg_names.append(eco)

                pkg_str = ", ".join(dict.fromkeys(pkg_names))
                if pkg_str:
                    combined_desc = f"[{pkg_str}] {combined_desc}"

                matched_stacks = match_stack_keywords(f"{combined_desc} {pkg_str}")
                pub_raw = adv.get("published_at") or ""
                pub_date = pub_raw[:10] if pub_raw else datetime.now(timezone.utc).strftime("%Y-%m-%d")

                results.append({
                    "cve_id": cve_id,
                    "published": pub_date,
                    "cvss_score": score,
                    "severity": severity,
                    "description_en": combined_desc,
                    "matched_stacks": matched_stacks,
                    "is_kev": False,
                    "url": f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                    "cve_org_url": adv.get("html_url") or f"https://www.cve.org/CVERecord?id={cve_id}",
                })
        except Exception as e:
            logger.warning(f"[CVEScraper] GitHub Advisories fetch warning: {e}")

        return results

    def _should_include(self, item: dict, custom_keyword: str = None) -> bool:
        """
        필터 조건:
        - custom_keyword 지정 시: 해당 키워드가 설명이나 스택, CVE ID에 포함된 경우
        - 기본 모드:
          1) CISA KEV(실제 악용 확인된 보안 사고) 등록건
          2) Critical 전체 (CVSS >= 9.0 또는 severity == CRITICAL)
          3) 관심 스택(Linux, Web/APM, DB, AWS, Docker 등) 매칭 & (CVSS >= 7.0 또는 HIGH/CRITICAL 또는 미채점 신규)
        """
        if custom_keyword:
            kw_lower = custom_keyword.lower()
            haystack = f"{item.get('cve_id', '')} {item.get('description_en', '')} {' '.join(item.get('matched_stacks', []))}".lower()
            return kw_lower in haystack

        if item.get("is_kev"):
            return True

        score = item.get("cvss_score")
        severity = (item.get("severity") or "").upper()
        matched_stacks = item.get("matched_stacks") or []

        # 1. Critical 전체 포함
        if (score is not None and score >= 9.0) or "CRITICAL" in severity:
            return True

        # 2. 내 관심 스택 매칭 시 (High 이상 또는 아직 점수가 부여되지 않은 따끈따끈한 신규 스택 CVE)
        if matched_stacks:
            if score is None or score >= 7.0 or severity in ("HIGH", "CRITICAL"):
                return True

        return False

    def fetch_Filtered_cves(self, limit: int = 5, lookback_hours: int = 48, exclude_ids: set = None, custom_keyword: str = None) -> list:
        """
        다중 소스(CISA KEV + NVD + GitHub Advisories)에서 최신 CVE를 수집·병합·필터링하여 반환합니다.
        """
        exclude_ids = exclude_ids or set()
        merged = {}

        # 1. CISA KEV (실제 악용 사고)
        kev_items = self.fetch_cisa_kev_recent(days=7)
        for cve_id, item in kev_items.items():
            merged[cve_id] = item

        # 2. NVD API 2.0
        nvd_items = self.fetch_nvd_recent(lookback_hours=lookback_hours)
        for item in nvd_items:
            cid = item["cve_id"]
            if cid in merged:
                # 이미 KEV에 있으면 점수/설명 보강
                if item.get("cvss_score") and merged[cid].get("cvss_score") == 9.5:
                    merged[cid]["cvss_score"] = item["cvss_score"]
                if item.get("matched_stacks"):
                    merged[cid]["matched_stacks"] = list(dict.fromkeys(merged[cid]["matched_stacks"] + item["matched_stacks"]))
            else:
                merged[cid] = item

        # 3. GitHub Security Advisories (보조 및 신속 수집)
        gh_items = self.fetch_github_advisories_recent(per_page=40)
        for item in gh_items:
            cid = item["cve_id"]
            if cid in merged:
                if merged[cid].get("cvss_score") is None and item.get("cvss_score") is not None:
                    merged[cid]["cvss_score"] = item["cvss_score"]
                    merged[cid]["severity"] = item["severity"]
                if item.get("matched_stacks"):
                    merged[cid]["matched_stacks"] = list(dict.fromkeys(merged[cid]["matched_stacks"] + item["matched_stacks"]))
            else:
                merged[cid] = item

        # 중복 발송 제외 및 필터링
        candidates = []
        for cid, item in merged.items():
            if cid in exclude_ids:
                continue
            if self._should_include(item, custom_keyword=custom_keyword):
                candidates.append(item)

        # 우선순위 정렬: 1순위 CISA KEV(실제 사고) -> 2순위 관심 스택 매칭 -> 3순위 CVSS 점수 높은 순 -> 최신 CVE 번호 순
        def sort_key(x):
            is_kev = 1 if x.get("is_kev") else 0
            has_stack = 1 if x.get("matched_stacks") else 0
            score = x.get("cvss_score") if x.get("cvss_score") is not None else 7.0
            return (is_kev, has_stack, score, x.get("cve_id", ""))

        candidates.sort(key=sort_key, reverse=True)
        return candidates[:limit]

    @staticmethod
    def format_cve_message(cve_item: dict, ko_summary: str = None) -> str:
        """원문 기반 간결한 텔레그램 알림 메시지 포맷 생성 (Markdown 대신 안전한 HTML 또는 깔끔한 텍스트)"""
        cve_id = cve_item.get("cve_id", "CVE-UNKNOWN")
        score = cve_item.get("cvss_score")
        severity = cve_item.get("severity") or "UNKNOWN"
        is_kev = cve_item.get("is_kev", False)
        stacks = cve_item.get("matched_stacks") or []
        published = cve_item.get("published", "")
        desc_en = (cve_item.get("description_en") or "").strip()
        if len(desc_en) > 360:
            desc_en = desc_en[:357] + "..."

        nvd_url = cve_item.get("url") or f"https://nvd.nist.gov/vuln/detail/{cve_id}"
        cve_org_url = cve_item.get("cve_org_url") or f"https://www.cve.org/CVERecord?id={cve_id}"

        score_str = f"{score:.1f} ({severity})" if isinstance(score, (int, float)) else f"미산정 ({severity})"
        if is_kev:
            header_icon = "🚨 [실제 악용 사고 / CISA KEV]"
        elif (isinstance(score, (int, float)) and score >= 9.0) or "CRITICAL" in severity:
            header_icon = "🔴 [CRITICAL]"
        else:
            header_icon = "🟠 [스택 주요 취약점]"

        lines = [
            f"{header_icon} {cve_id}",
            f"• 위험도(CVSS): {score_str}",
        ]
        if stacks:
            lines.append(f"• 관련 스택: {', '.join(stacks)}")
        if published:
            lines.append(f"• 등록일: {published}")

        if ko_summary:
            lines.append(f"\n🇰🇷 한글 요약:\n{ko_summary.strip()}")

        lines.append(f"\n🌐 영문 원문:\n{desc_en}")
        lines.append(f"\n🔗 상세 링크: {nvd_url}\n🔗 참조: {cve_org_url}")

        return "\n".join(lines)
