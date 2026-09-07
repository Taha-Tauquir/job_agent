from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urljoin, urlsplit

from lxml import etree, html

from job_agent.errors import ConnectorError
from job_agent.models import NormalizedJob
from job_agent.utils import fallback_job_id, infer_remote, join_nonempty, parse_datetime


_SALARY_RE = re.compile(
    r"£\s*(?P<minimum>[\d,.]+)\s*(?P<min_k>k)?(?:\s*[-–]\s*£?\s*(?P<maximum>[\d,.]+)\s*(?P<max_k>k)?)?\s*/\s*(?P<interval>[A-Za-z]+)",
    re.I,
)


class SponsoredJobsParser:
    """Parse sponsoredjobs.co.uk listing cards and complete detail pages."""

    def parse_listing(
        self,
        page_html: str,
        *,
        page_url: str,
        source: str,
    ) -> tuple[list[NormalizedJob], str | None, int | None]:
        if not page_html.strip():
            raise ConnectorError("Sponsored Jobs returned an empty listing page")
        document = html.fromstring(page_html)
        embedded_rows = self._extract_embedded_jobs(document)
        if embedded_rows is not None:
            jobs = [
                self._parse_embedded_job(row, page_url=page_url, source=source)
                for row in embedded_rows
            ]
            return jobs, None, len(jobs)

        anchors = document.xpath(
            '//div[contains(concat(" ", normalize-space(@class), " "), " group ")]'
            '/a[starts-with(@href, "/jobs/") and .//h2]'
        )
        if not anchors:
            raise ConnectorError("Sponsored Jobs returned no recognizable job cards")

        jobs = [self._parse_card(anchor, page_url=page_url, source=source) for anchor in anchors]
        next_cursor = self._next_page(document)
        text = " ".join(document.text_content().split())
        total_match = re.search(r"Showing\s+\d+\s+of\s+(\d+)\s+roles", text, re.I)
        total = int(total_match.group(1)) if total_match else None
        return jobs, next_cursor, total

    @staticmethod
    def _extract_embedded_jobs(document) -> list[dict] | None:
        """Read the complete Next.js Flight `filteredJobs` property.

        The rendered card DOM can contain only a partial page or duplicate
        cards. The server payload contains the complete filtered result set and
        is therefore the authoritative listing source.
        """
        marker = '"filteredJobs":'
        decoder = json.JSONDecoder()
        scripts = document.xpath('//script[contains(text(), "self.__next_f.push")]')
        for script in scripts:
            script_text = script.text or ""
            match = re.search(r"self\.__next_f\.push\((.*)\)\s*$", script_text, re.S)
            if not match:
                continue
            try:
                flight_entry = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if len(flight_entry) < 2 or not isinstance(flight_entry[1], str):
                continue
            chunk = flight_entry[1]
            marker_index = chunk.find(marker)
            if marker_index < 0:
                continue
            try:
                rows, _ = decoder.raw_decode(chunk, marker_index + len(marker))
            except json.JSONDecodeError:
                continue
            if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
                return rows
        return None

    @staticmethod
    def _parse_embedded_job(
        row: dict,
        *,
        page_url: str,
        source: str,
    ) -> NormalizedJob:
        slug = str(row.get("slug") or "").strip()
        title = row.get("title") or "Untitled job"
        company = row.get("company") or "Unknown employer"
        job_url = urljoin(page_url, f"/jobs/{slug}")
        salary = row.get("salary") or {}
        salary_min = salary.get("min")
        salary_max = salary.get("max")
        salary_currency = salary.get("currency")
        salary_interval = salary.get("unit")
        salary_text = None
        if salary_min is not None:
            minimum = SponsoredJobsParser._compact_salary(salary_min, salary_currency)
            maximum = SponsoredJobsParser._compact_salary(salary_max, salary_currency)
            salary_text = minimum
            if salary_max is not None and salary_max != salary_min:
                salary_text = f"{minimum}-{maximum}"
            if salary_interval:
                salary_text = f"{salary_text}/{salary_interval}"

        location = row.get("location_text") or join_nonempty(
            [row.get("workplace_city"), row.get("workplace_region"), row.get("workplace_country")]
        )
        tier = str(row.get("sponsorship_tier") or "").casefold()
        sponsorship = row.get("visa_sponsorship")
        if tier == "verified":
            sponsorship = "Sponsors visa"
        elif tier == "eligible":
            sponsorship = "Potentially sponsorable"
        visa_route = row.get("visa_route")
        soc_code = str(row.get("soc_code") or "").strip()
        meets_threshold = row.get("salary_meets_threshold")
        workplace_type = row.get("workplace_type")
        explicitly_remote = (
            str(workplace_type).casefold() == "remote" if workplace_type else None
        )
        return NormalizedJob(
            source=source,
            source_job_id=str(
                row.get("id")
                or row.get("job_identifier")
                or slug
                or fallback_job_id(job_url, title, company)
            ),
            title=title,
            company=company,
            location=location,
            description=row.get("description") or None,
            salary_min=float(salary_min) if salary_min is not None else None,
            salary_max=float(salary_max) if salary_max is not None else None,
            salary_currency=salary_currency,
            salary_interval=salary_interval,
            salary_text=salary_text,
            employment_type=row.get("type"),
            workplace_type=workplace_type,
            remote=infer_remote(location, explicitly_remote),
            posted_at=parse_datetime(row.get("posted_date")),
            expires_at=parse_datetime(row.get("valid_through")),
            application_deadline=parse_datetime(row.get("valid_through")),
            sponsorship_status=sponsorship,
            visa_routes=[visa_route] if visa_route else [],
            soc_code=f"SOC {soc_code}" if soc_code else None,
            going_rate_status=(
                "Above going rate" if meets_threshold is True else
                "Below going rate" if meets_threshold is False else None
            ),
            sector=row.get("industry"),
            job_url=job_url,
            apply_url=row.get("apply_url") or job_url,
            provider_data={"listing_record": row},
        )

    @staticmethod
    def _compact_salary(value: object, currency: str | None) -> str:
        symbol = "£" if currency == "GBP" else f"{currency} " if currency else ""
        number = float(value)
        rendered = f"{number / 1000:g}k" if number >= 1000 else f"{number:g}"
        return f"{symbol}{rendered}"

    def parse_detail(self, page_html: str, *, job: NormalizedJob) -> NormalizedJob:
        if not page_html.strip():
            raise ConnectorError("Sponsored Jobs returned an empty job-detail page")
        document = html.fromstring(page_html)
        detail_rows = self._detail_rows(document)
        prose_nodes = document.xpath('//article//div[contains(@class, "job-prose")]')
        prose = prose_nodes[0] if prose_nodes else None

        title = self._first_text(document, "//h1") or job.title
        sector = self._first_text(document, '//a[starts-with(@href, "/jobs/sector/")]')
        company = self._detail_company(document) or job.company
        apply_anchor = self._first_node(
            document,
            '//a[contains(normalize-space(.), "Apply on company site")]',
        )
        apply_url = apply_anchor.get("href") if apply_anchor is not None else job.apply_url
        salary_text = detail_rows.get("Salary") or job.salary_text
        salary_min, salary_max, salary_interval = self.parse_salary(salary_text)
        sponsorship = detail_rows.get("Sponsorship") or job.sponsorship_status
        visa_routes = self._visa_routes(document, sponsorship, job.visa_routes)
        note = self._first_text(document, '//p[@role="note"]')
        employer_anchor = self._first_node(
            document,
            '//h3[normalize-space()="Employer\'s website"]/following-sibling::p[1]//a',
        )
        source_anchor = self._detail_row_anchor(document, "Job Source")
        description = " ".join(prose.text_content().split()) if prose is not None else job.description
        description_html = (
            etree.tostring(prose, encoding="unicode", method="html") if prose is not None else None
        )
        posted = self._parse_human_date(detail_rows.get("Date Posted")) or job.posted_at
        deadline = self._parse_human_date(detail_rows.get("Application Deadline"))
        location = detail_rows.get("Job Location") or job.location
        workplace_type = detail_rows.get("Workplace Type") or job.workplace_type

        provider_data = dict(job.provider_data)
        provider_data.update(
            {
                "job_source": self._text(source_anchor) if source_anchor is not None else None,
                "job_source_url": source_anchor.get("href") if source_anchor is not None else None,
                "employer_website": employer_anchor.get("href") if employer_anchor is not None else None,
                "sponsor_licence_note": note,
                "detail_fields": detail_rows,
            }
        )
        return job.model_copy(
            update={
                "title": title,
                "company": company,
                "location": location,
                "description": description,
                "description_html": description_html,
                "salary_min": salary_min if salary_min is not None else job.salary_min,
                "salary_max": salary_max if salary_max is not None else job.salary_max,
                "salary_currency": "GBP" if salary_text else job.salary_currency,
                "salary_interval": salary_interval or job.salary_interval,
                "salary_text": salary_text,
                "employment_type": self._header_employment_type(document) or job.employment_type,
                "workplace_type": workplace_type,
                "remote": infer_remote(location, workplace_type and workplace_type.casefold() == "remote"),
                "posted_at": posted,
                "expires_at": deadline or job.expires_at,
                "application_deadline": deadline,
                "sponsorship_status": sponsorship,
                "visa_routes": visa_routes,
                "sector": sector or job.sector,
                "apply_url": apply_url,
                "provider_data": provider_data,
            }
        )

    def _parse_card(self, anchor, *, page_url: str, source: str) -> NormalizedJob:
        href = anchor.get("href")
        job_url = urljoin(page_url, href)
        title = self._first_text(anchor, ".//h2") or "Untitled job"
        company = self._first_text(
            anchor,
            './/div[contains(@class, "text-brand-text-muted") and contains(@class, "text-sm")]',
        ) or "Unknown employer"
        badges = self._unique_texts(anchor.xpath('.//span[contains(@style, "letter-spacing")]'))
        metadata = self._card_metadata(anchor)
        salary_text = next((value for value in metadata if "£" in value), None)
        employment_type = next(
            (value for value in metadata if value.casefold() in {"full-time", "part-time", "contract", "temporary"}),
            None,
        )
        location = next(
            (value for value in metadata if value not in {salary_text, employment_type}),
            None,
        )
        sponsorship = next(
            (value for value in badges if "sponsor" in value.casefold()),
            None,
        )
        soc_code = next((value for value in badges if re.fullmatch(r"SOC\s+\d+", value, re.I)), None)
        visa_routes = [value for value in badges if value.casefold() in {"skilled_worker", "health_and_care"}]
        going_rate = next((value for value in badges if "going rate" in value.casefold()), None)
        posted_node = self._first_node(anchor, ".//time")
        posted = self._parse_human_date(posted_node.get("datetime") if posted_node is not None else None)
        salary_min, salary_max, salary_interval = self.parse_salary(salary_text)
        slug = urlsplit(job_url).path.rstrip("/").rsplit("/", 1)[-1]
        return NormalizedJob(
            source=source,
            source_job_id=slug or fallback_job_id(job_url, title, company),
            title=title,
            company=company,
            location=location,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency="GBP" if salary_text else None,
            salary_interval=salary_interval,
            salary_text=salary_text,
            employment_type=employment_type,
            remote=infer_remote(location),
            posted_at=posted,
            sponsorship_status=sponsorship,
            visa_routes=visa_routes,
            soc_code=soc_code,
            going_rate_status=going_rate,
            job_url=job_url,
            apply_url=job_url,
            provider_data={"listing_badges": badges},
        )

    @staticmethod
    def parse_salary(value: str | None) -> tuple[float | None, float | None, str | None]:
        match = _SALARY_RE.search(value or "")
        if not match:
            return None, None, None

        def amount(group: str, thousands_group: str) -> float | None:
            raw = match.group(group)
            if not raw:
                return None
            number = float(raw.replace(",", ""))
            return number * 1000 if match.group(thousands_group) else number

        minimum = amount("minimum", "min_k")
        maximum = amount("maximum", "max_k") or minimum
        interval = match.group("interval").casefold()
        return minimum, maximum, interval

    @staticmethod
    def _next_page(document) -> str | None:
        nodes = document.xpath('//a[@aria-label="Go to next page"]')
        if not nodes or "pointer-events-none" in (nodes[0].get("class") or "").split():
            return None
        query = dict(parse_qsl(urlsplit(nodes[0].get("href") or "").query))
        return query.get("page")

    @staticmethod
    def _card_metadata(anchor) -> list[str]:
        rows = anchor.xpath('.//div[contains(@class, "gap-x-2") and contains(@class, "gap-y-1")]')
        if not rows:
            return []
        values = []
        for node in rows[0].xpath("./span"):
            value = " ".join(node.text_content().split())
            if value and value != "•":
                values.append(value)
        return values

    @staticmethod
    def _detail_rows(document) -> dict[str, str]:
        rows: dict[str, str] = {}
        sections = document.xpath('//section[.//h2[normalize-space()="Job Details"]]')
        if not sections:
            return rows
        for row in sections[0].xpath('.//div[count(./span) >= 2]'):
            spans = row.xpath("./span")
            label = " ".join(spans[0].text_content().split())
            value = " ".join(spans[1].text_content().split())
            if label and value:
                rows[label] = value
        return rows

    @staticmethod
    def _detail_row_anchor(document, label: str):
        nodes = document.xpath(
            f'//section[.//h2[normalize-space()="Job Details"]]//div[./span[1][normalize-space()="{label}"]]//span[2]//a'
        )
        return nodes[0] if nodes else None

    @staticmethod
    def _detail_company(document) -> str | None:
        sectors = document.xpath('//a[starts-with(@href, "/jobs/sector/")]')
        if not sectors:
            return None
        parent = sectors[0].getparent()
        previous = parent.getprevious() if parent is not None else None
        return " ".join(previous.text_content().split()) if previous is not None else None

    @staticmethod
    def _header_employment_type(document) -> str | None:
        section = document.xpath("//section[.//h1]")
        if not section:
            return None
        values = SponsoredJobsParser._unique_texts(section[0].xpath('.//span[contains(@style, "letter-spacing")]'))
        return next(
            (value for value in values if value.casefold() in {"full-time", "part-time", "contract", "temporary"}),
            None,
        )

    @staticmethod
    def _visa_routes(document, sponsorship: str | None, existing: list[str]) -> list[str]:
        values = [value.replace("_", " ").title() for value in existing]
        values.extend(
            SponsoredJobsParser._unique_texts(
                document.xpath(
                    '//section[.//h1]//a[starts-with(@href, "/jobs/visa-route/")]'
                )
            )
        )
        if sponsorship and "·" in sponsorship:
            values.append(sponsorship.split("·", 1)[1].strip())
        unique: dict[str, str] = {}
        for value in values:
            if value:
                unique.setdefault(value.casefold(), value)
        return list(unique.values())

    @staticmethod
    def _parse_human_date(value: str | None) -> datetime | None:
        if not value:
            return None
        candidate = re.sub(r"\s*\([^)]*\)\s*$", "", value).strip()
        candidate = re.sub(r"\s+00:00:00\+00$", "", candidate)
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S%z", "%b %d, %Y", "%d %B %Y"):
            try:
                parsed = datetime.strptime(candidate, fmt)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _first_node(node, xpath: str):
        nodes = node.xpath(xpath)
        return nodes[0] if nodes else None

    @staticmethod
    def _first_text(node, xpath: str) -> str | None:
        result = SponsoredJobsParser._first_node(node, xpath)
        return SponsoredJobsParser._text(result) if result is not None else None

    @staticmethod
    def _text(node) -> str:
        return " ".join(node.text_content().split())

    @staticmethod
    def _unique_texts(nodes) -> list[str]:
        return list(dict.fromkeys(SponsoredJobsParser._text(node) for node in nodes))
