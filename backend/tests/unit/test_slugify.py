"""Organization slugs (TASK-03.04)."""

import pytest

from app.services.organizations import SLUG_MAX_LENGTH, slugify


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Acme Research", "acme-research"),
        ("Acme Corp. (EU)", "acme-corp-eu"),
        ("Alice Smith's workspace", "alice-smiths-workspace"),
        ("Alice Smith\u2019s workspace", "alice-smiths-workspace"),  # typographic apostrophe
        ("Café Crème", "cafe-creme"),
        ("  --Hello__World--  ", "hello-world"),
        ("東京", "org"),  # nothing ASCII-representable left
        ("!!!", "org"),
    ],
)
def test_slugify(name: str, slug: str) -> None:
    assert slugify(name) == slug


def test_slug_length_is_capped_without_trailing_hyphen() -> None:
    slug = slugify("word " * 50)

    assert len(slug) <= SLUG_MAX_LENGTH
    assert not slug.endswith("-")
