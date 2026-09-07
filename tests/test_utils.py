from job_agent.utils import contains_search_term


def test_search_term_does_not_match_longer_programming_language() -> None:
    assert contains_search_term("Backend development with Java and Go", "java")
    assert not contains_search_term("Frontend development with JavaScript", "java")


def test_search_term_supports_phrases_and_punctuation() -> None:
    assert contains_search_term("We use Spring Boot-based services", "spring boot")
    assert contains_search_term("Experience with C++ is useful", "c++")
