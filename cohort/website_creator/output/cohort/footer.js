// Single source of truth for site footer.
// Edit this to update footer across all pages.
var FOOTER_CONFIG = {
    brand: {
        name: "COHORT",
        tagline: "AI Team Coordination. Your agents, thinking together."
    },
    columns: [
        {
            heading: "Product",
            links: [
                { label: "How It Works", href: "features.html" },
                { label: "The Tools", href: "tools.html" },
                { label: "Use Cases", href: "use-cases.html" },
                { label: "Content Pipeline", href: "marketing.html" },
                { label: "Pricing", href: "pricing.html" }
            ]
        },
        {
            heading: "Resources",
            links: [
                { label: "Documentation", href: "docs.html" },
                { label: "Benchmarks", href: "benchmarks.html" },
                { label: "Compare", href: "compare.html" },
                { label: "AI's Take", href: "ai-perspective.html" },
                { label: "GitHub", href: "https://github.com/cohort-dev/cohort", external: true }
            ]
        },
        {
            heading: "Connect",
            links: [
                { label: "Contact", href: "contact.html" },
                { label: "hello@cohort.dev", href: "mailto:hello@cohort.dev" },
                { label: "Twitter/X", href: "https://twitter.com/cohort_dev", external: true },
                { label: "Discord", href: "https://discord.gg/cohort", external: true }
            ]
        }
    ],
    bottom: {
        copyright: "&copy; 2026 Cohort. Open source under <a href=\"https://github.com/cohort-dev/cohort/blob/main/LICENSE\" style=\"color: rgba(255,255,255,0.7);\">MIT License</a>.",
        badge: "[*] Built with Cohort"
    }
};

document.addEventListener("DOMContentLoaded", function () {
    var footer = document.querySelector(".site-footer");
    if (!footer) return;

    var brandHtml =
        '<div>' +
            '<h4 style="color:#fff; margin-bottom:1rem;">' + FOOTER_CONFIG.brand.name + '</h4>' +
            '<p>' + FOOTER_CONFIG.brand.tagline + '</p>' +
        '</div>';

    var columnsHtml = FOOTER_CONFIG.columns.map(function (col) {
        var linksHtml = col.links.map(function (link) {
            var attrs = link.external ? ' target="_blank" rel="noopener"' : '';
            return '<li style="margin-bottom:0.5rem;"><a href="' + link.href + '"' + attrs + '>' + link.label + '</a></li>';
        }).join("\n");
        return '<div>' +
            '<h4 style="color:#fff; margin-bottom:1rem;">' + col.heading + '</h4>' +
            '<ul style="list-style:none; padding:0; margin:0;">' + linksHtml + '</ul>' +
        '</div>';
    }).join("\n");

    footer.innerHTML =
        '<div class="container">' +
            '<div class="footer-grid">' + brandHtml + columnsHtml + '</div>' +
            '<div class="footer-bottom">' +
                '<p>' + FOOTER_CONFIG.bottom.copyright + '</p>' +
                '<div class="cohort-badge">' + FOOTER_CONFIG.bottom.badge + '</div>' +
            '</div>' +
        '</div>';
});
