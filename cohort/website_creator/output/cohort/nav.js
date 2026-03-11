// Single source of truth for site navigation.
// Edit this array to update nav across all pages.
const NAV_ITEMS = [
    { label: "How It Works", href: "features.html" },
    { label: "Tools", href: "tools.html" },
    { label: "Use Cases", href: "use-cases.html" },
    { label: "Content Pipeline", href: "marketing.html" },
    { label: "Pricing", href: "pricing.html" },
    { label: "Benchmarks", href: "benchmarks.html" },
    { label: "Docs", href: "docs.html" },
    { label: "Compare", href: "compare.html" },
    { label: "AI's Take", href: "ai-perspective.html" },
    { label: "Contact", href: "contact.html" }
];

document.addEventListener("DOMContentLoaded", function () {
    const ul = document.querySelector(".nav-links");
    if (!ul) return;
    ul.innerHTML = NAV_ITEMS.map(function (item) {
        return '<li><a href="' + item.href + '">' + item.label + '</a></li>';
    }).join("\n");
});
