from scripts.sync_web_knowledge import _parser


def test_sync_web_knowledge_parser_supports_bounded_seed_batches() -> None:
    args = _parser().parse_args(
        [
            "--tenant-id",
            "tenant-demo",
            "--site-id",
            "demo-store-shadow",
            "--base-url",
            "https://shop.example.com",
            "--url",
            "https://shop.example.com/product-a.html",
            "--url",
            "https://shop.example.com/product-b.html",
            "--sitemap-url",
            "https://shop.example.com/sitemap-products.xml",
            "--max-pages",
            "20",
            "--max-sitemaps",
            "1",
            "--batch-size",
            "20",
            "--skip-sitemap",
            "--no-follow-internal-links",
        ]
    )

    assert args.tenant_id == "tenant-demo"
    assert args.site_id == "demo-store-shadow"
    assert args.url == [
        "https://shop.example.com/product-a.html",
        "https://shop.example.com/product-b.html",
    ]
    assert args.sitemap_url == ["https://shop.example.com/sitemap-products.xml"]
    assert args.max_pages == 20
    assert args.max_sitemaps == 1
    assert args.batch_size == 20
    assert args.skip_sitemap is True
    assert args.no_follow_internal_links is True
