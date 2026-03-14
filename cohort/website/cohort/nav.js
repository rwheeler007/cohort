// Inject favicon from central location (works with local file:// and hosted)
(function () {
    if (!document.querySelector('link[rel="icon"]')) {
        var link = document.createElement("link");
        link.rel = "icon";
        link.type = "image/svg+xml";
        link.href = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect x='3' y='4' width='26' height='18' rx='3' fill='%23D97757'/%3E%3Cpolygon points='8,22 14,22 10,28' fill='%23D97757'/%3E%3Ctext x='16' y='17' text-anchor='middle' font-family='monospace' font-weight='bold' font-size='14' fill='%231a1d21'%3ECO%3C/text%3E%3C/svg%3E";
        document.head.appendChild(link);
    }
})();

// Single source of truth for site navigation.
// Edit this array to update nav across all pages.
// Items with `children` render as grouped dropdowns.
const NAV_ITEMS = [
    {
        label: "How It Works", children: [
            { label: "Features", href: "features.html" },
            { label: "Use Cases", href: "use-cases.html" },
            { label: "Conversations", href: "conversations.html" }
        ]
    },
    { label: "Pricing", href: "pricing.html" },
    {
        label: "Docs", children: [
            { label: "Documentation", href: "docs.html" },
            { label: "Benchmarks", href: "benchmarks.html" },
            { label: "Compare", href: "compare.html" }
        ]
    },
    {
        label: "The Tools", children: [
            { label: "Overview", href: "tools.html" },
            { label: "MCP Tools", href: "mcp-tools.html" },
            { label: "CLI", href: "cli.html" },
            { label: "Content Pipeline", href: "marketing.html" },
            { label: "Website Creator", href: "website-creator.html" },
            { label: "Project Integration", href: "project-integration.html" }
        ]
    },
    {
        label: "The Team", children: [
            { label: "Meet the Agents", href: "team.html" },
            { label: "AI's Take", href: "ai-perspective.html" }
        ]
    },
];

document.addEventListener("DOMContentLoaded", function () {
    var ul = document.querySelector(".nav-links");
    if (!ul) return;

    ul.innerHTML = NAV_ITEMS.map(function (item) {
        if (!item.children) {
            return '<li><a href="' + item.href + '">' + item.label + '</a></li>';
        }
        var subs = item.children.map(function (child) {
            return '<li><a href="' + child.href + '">' + child.label + '</a></li>';
        }).join("\n");
        return '<li class="nav-dropdown">' +
            '<button class="nav-dropdown-toggle" aria-expanded="false">' +
                item.label + ' <span class="nav-caret">&#9662;</span>' +
            '</button>' +
            '<ul class="nav-dropdown-menu">' + subs + '</ul>' +
        '</li>';
    }).join("\n");

    // Desktop: open/close on click, close when clicking outside
    document.addEventListener("click", function (e) {
        var toggle = e.target.closest(".nav-dropdown-toggle");
        var allDropdowns = ul.querySelectorAll(".nav-dropdown");

        if (toggle) {
            e.preventDefault();
            var parent = toggle.closest(".nav-dropdown");
            var isOpen = parent.classList.contains("open");

            // Close all first
            allDropdowns.forEach(function (d) {
                d.classList.remove("open");
                d.querySelector(".nav-dropdown-toggle").setAttribute("aria-expanded", "false");
            });

            // Toggle the clicked one
            if (!isOpen) {
                parent.classList.add("open");
                toggle.setAttribute("aria-expanded", "true");
            }
        } else {
            // Click outside -- close all
            allDropdowns.forEach(function (d) {
                d.classList.remove("open");
                d.querySelector(".nav-dropdown-toggle").setAttribute("aria-expanded", "false");
            });
        }
    });
});
