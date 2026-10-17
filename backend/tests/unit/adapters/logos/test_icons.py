"""Which icons a website declares and in which order they are tried."""

from papiq.adapters.outbound.logos.icons import (
    Candidate,
    declared_size,
    manifest_icons,
    page_icons,
    ranked,
)

PAGE = "https://nord.example/de/start"


def test_sizes() -> None:
    assert declared_size("16x16 32x32") == 32
    assert declared_size("180X180") == 180
    assert declared_size("192x96") == 192
    assert declared_size("any") is None
    assert declared_size(None) is None
    assert declared_size("big") is None
    assert declared_size("²x²") is None  # digits, but not ASCII ones
    assert declared_size("1" * 5000 + "x1") is None


def test_reads_the_icons_of_a_page() -> None:
    html = """<!doctype html><html><head>
      <link rel="icon" href="/favicon-32.png" sizes="32x32" type="image/png">
      <link rel="icon" href="icon.svg" type="image/svg+xml">
      <link rel="icon" href="/vector" type="image/svg+xml" sizes="any">
      <link rel="mask-icon" href="/mask.png">
      <LINK REL="Apple-Touch-Icon" HREF="/apple.png">
      <link rel="shortcut icon" href="https://cdn.nord.example/fav.ico">
      <link rel="icon" href="http://nord.example/plain.png" sizes="512x512">
      <link rel="icon" href="data:image/png;base64,AAAA" sizes="256x256">
      <link rel="icon" href="javascript:alert(1)">
      <link rel="manifest" href="/site.webmanifest">
      <link rel="stylesheet" href="/style.css">
    </head><body><link rel="icon" href="/late.png" sizes="96x96"></body></html>"""
    icons, manifest = page_icons(html, PAGE)
    assert manifest == "https://nord.example/site.webmanifest"
    assert icons == [
        Candidate("https://nord.example/favicon-32.png", 32),
        Candidate("https://nord.example/apple.png", 180),
        Candidate("https://cdn.nord.example/fav.ico", None),
        Candidate("https://nord.example/late.png", 96),
    ]


def test_a_base_and_a_broken_page() -> None:
    base = '<base href="https://static.nord.example/x/"><link rel=icon href=a.png>'
    assert page_icons(base, PAGE)[0] == [Candidate("https://static.nord.example/x/a.png", None)]
    # Whatever a broken page makes of it, reading it does not fail.
    icons, manifest = page_icons("<<<>>><link rel='icon' href='/x.png' <!-- <link", PAGE)
    assert isinstance(icons, list) and manifest is None


def test_reads_the_icons_of_a_manifest() -> None:
    manifest = """{"icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "icons/512.png", "sizes": "512x512", "purpose": "maskable"},
        {"src": "/icon-512.png", "sizes": "512x512", "purpose": "any maskable"},
        {"src": "/mono.png", "sizes": "512x512", "purpose": "monochrome"},
        {"src": "/logo.svg", "sizes": "any"},
        {"src": 7}, "nonsense"
    ]}"""
    icons = manifest_icons(manifest, "https://nord.example/app/site.webmanifest")
    assert icons == [
        Candidate("https://nord.example/icon-192.png", 192),
        Candidate("https://nord.example/app/icons/512.png", 512, 1),
        Candidate("https://nord.example/icon-512.png", 512),
    ]
    assert manifest_icons("not json", "https://nord.example/m.json") == []
    assert manifest_icons('{"icons": "x"}', "https://nord.example/m.json") == []
    assert manifest_icons("[1, 2]", "https://nord.example/m.json") == []
    assert manifest_icons("[" * 100_000, "https://nord.example/m.json") == []


def test_tries_the_largest_first_and_the_favicon_last() -> None:
    favicon = "https://nord.example/favicon.ico"
    order = ranked(
        [
            Candidate("https://nord.example/32.png", 32),  # too small to try
            Candidate("https://nord.example/unknown.png", None),
            Candidate("https://nord.example/apple.png", 180),
            Candidate("https://nord.example/maskable.png", 512, 1),
            Candidate("https://nord.example/512.png", 512),
            Candidate("https://nord.example/64.png", 64),
            Candidate(favicon, None),
        ],
        favicon,
    )
    assert order == [
        "https://nord.example/512.png",
        "https://nord.example/maskable.png",
        "https://nord.example/apple.png",
        "https://nord.example/64.png",
        "https://nord.example/unknown.png",
        favicon,
    ]
    assert ranked([], favicon) == [favicon]
