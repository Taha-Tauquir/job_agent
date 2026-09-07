from job_agent.parsers import LinkedInSearchParser

HTML = """
<ul><li><div class="base-card" data-entity-urn="urn:li:jobPosting:4444978733">
  <a class="base-card__full-link" href="https://uk.linkedin.com/jobs/view/java-engineer-4444978733?trackingId=test"></a>
  <h3 class="base-search-card__title">Java Engineer</h3>
  <h4 class="base-search-card__subtitle">Example Bank</h4>
  <span class="job-search-card__location">London, United Kingdom</span>
  <time datetime="2026-09-06"></time>
</div></li></ul>
"""


def test_linkedin_parser_can_be_tested_without_http() -> None:
    jobs = LinkedInSearchParser().parse(
        HTML,
        source="linkedin-public",
        configured_search_url="https://www.linkedin.com/jobs/search/?keywords=java",
        allow_empty=False,
    )

    assert len(jobs) == 1
    assert jobs[0].source_job_id == "4444978733"
    assert jobs[0].title == "Java Engineer"
    assert jobs[0].company == "Example Bank"
    assert jobs[0].location == "London, United Kingdom"
