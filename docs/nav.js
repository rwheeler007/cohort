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

// Inject og:image if missing (social preview for all pages)
(function () {
    if (!document.querySelector('meta[property="og:image"]')) {
        var meta = document.createElement("meta");
        meta.setAttribute("property", "og:image");
        meta.content = "https://rwheeler007.github.io/cohort/images/cohort-social-preview.png";
        document.head.appendChild(meta);
    }
    if (!document.querySelector('meta[name="twitter:image"]')) {
        var meta = document.createElement("meta");
        meta.setAttribute("name", "twitter:image");
        meta.content = "https://rwheeler007.github.io/cohort/images/cohort-social-preview.png";
        document.head.appendChild(meta);
    }
})();

// Inject Prism.js for syntax highlighting (if code blocks exist on page)
(function () {
    if (document.querySelector('pre code, .code-block code')) {
        var link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = "https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/themes/prism-tomorrow.min.css";
        document.head.appendChild(link);

        var script = document.createElement("script");
        script.src = "https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/prism.min.js";
        script.onload = function () {
            // Load common language components
            var langs = ["python", "yaml", "bash", "json", "javascript"];
            langs.forEach(function (lang) {
                var s = document.createElement("script");
                s.src = "https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-" + lang + ".min.js";
                document.head.appendChild(s);
            });
        };
        document.head.appendChild(script);
    }
})();

// Single source of truth for site navigation.
// Edit this array to update nav across all pages.
// Items with `children` render as grouped dropdowns.
const NAV_ITEMS = [
    { label: "Channels", href: "channels.html", highlight: true },
    {
        label: "How It Works", children: [
            { label: "Features", href: "features.html" },
            { label: "Use Cases", href: "use-cases.html" },
            { label: "Conversations", href: "conversations.html" },
            { label: "Try the Simulator", href: "simulator.html" }
        ]
    },
    {
        label: "The Tools", children: [
            { label: "Overview", href: "tools.html" },
            { label: "MCP Tools", href: "mcp-tools.html" },
            { label: "CLI", href: "cli.html" },
            { label: "Task System", href: "tasks.html" },
            { label: "Content Pipeline", href: "marketing.html" },
            { label: "Website Creator", href: "website-creator.html" },
            { label: "Project Integration", href: "project-integration.html" },
            { label: "VS Code Extension", href: "vscode.html" }
        ]
    },
    {
        label: "The Team", children: [
            { label: "Meet the Agents", href: "team.html" },
            { label: "AI's Take", href: "ai-perspective.html" }
        ]
    },
    // Pricing hidden until launch
    // {
    //     label: "Pricing", children: [
    //         { label: "Plans", href: "pricing.html" },
    //         { label: "ROI Calculator", href: "roi.html" }
    //     ]
    // },
    {
        label: "Docs", children: [
            { label: "Getting Started", href: "getting-started.html" },
            { label: "Documentation", href: "docs.html" },
            { label: "Download", href: "download.html" },
            { label: "Benchmarks", href: "benchmarks.html" },
            { label: "Mode Comparison", href: "benchmark-modes.html" },
            { label: "Compare", href: "compare.html" },
            { label: "Enterprise", href: "enterprise.html" },
            { label: "Security Scans", href: "security-scans.html" }
        ]
    },
    {
        label: "Contact", children: [
            { label: "Contact Us", href: "contact.html" },
            { label: "Report Issue", href: "https://github.com/rwheeler007/cohort/issues" }
        ]
    },
];

document.addEventListener("DOMContentLoaded", function () {
    var ul = document.querySelector(".nav-links");
    if (!ul) return;

    var currentPage = window.location.pathname.split("/").pop() || "index.html";

    ul.innerHTML = NAV_ITEMS.map(function (item) {
        if (!item.children) {
            var active = (item.href === currentPage) ? ' class="nav-active"' : '';
            var style = item.highlight ? ' style="color: var(--color-primary); font-weight: 600;"' : '';
            return '<li><a href="' + item.href + '"' + active + style + '>' + item.label + '</a></li>';
        }
        var groupActive = item.children.some(function (c) { return c.href === currentPage; });
        var subs = item.children.map(function (child) {
            var active = (child.href === currentPage) ? ' class="nav-active"' : '';
            return '<li><a href="' + child.href + '"' + active + '>' + child.label + '</a></li>';
        }).join("\n");
        var toggleClass = groupActive ? ' nav-parent-active' : '';
        return '<li class="nav-dropdown">' +
            '<button class="nav-dropdown-toggle' + toggleClass + '" aria-expanded="false">' +
                item.label + ' <span class="nav-caret">&#9662;</span>' +
            '</button>' +
            '<ul class="nav-dropdown-menu" role="menu">' + subs + '</ul>' +
        '</li>';
    }).join("\n");

    // Add CTA buttons to nav bar
    var ctaLi = document.createElement("li");
    ctaLi.className = "nav-cta-group";
    ctaLi.innerHTML =
        '<a href="https://github.com/rwheeler007/cohort" class="nav-pip-btn"><span style="user-select: none;">$ </span>pip install</a>' +
        '<a href="https://github.com/rwheeler007/cohort/releases/tag/v0.4.4-vscode" class="nav-download-btn">Install VS Code Extension</a>';
    ul.appendChild(ctaLi);

    var allDropdowns = ul.querySelectorAll(".nav-dropdown");

    function closeAll() {
        allDropdowns.forEach(function (d) {
            d.classList.remove("open");
            d.querySelector(".nav-dropdown-toggle").setAttribute("aria-expanded", "false");
        });
    }

    function openDropdown(dropdown) {
        closeAll();
        dropdown.classList.add("open");
        dropdown.querySelector(".nav-dropdown-toggle").setAttribute("aria-expanded", "true");
    }

    // Desktop: open/close on click, close when clicking outside
    document.addEventListener("click", function (e) {
        var toggle = e.target.closest(".nav-dropdown-toggle");
        if (toggle) {
            e.preventDefault();
            var parent = toggle.closest(".nav-dropdown");
            var isOpen = parent.classList.contains("open");
            if (isOpen) { closeAll(); } else { openDropdown(parent); }
        } else {
            closeAll();
        }
    });

    // Keyboard navigation: Escape closes, ArrowDown/Up moves through menu items
    document.addEventListener("keydown", function (e) {
        var openDd = ul.querySelector(".nav-dropdown.open");

        if (e.key === "Escape" && openDd) {
            closeAll();
            openDd.querySelector(".nav-dropdown-toggle").focus();
            return;
        }

        // ArrowDown/Up within an open dropdown menu
        if (openDd && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
            e.preventDefault();
            var items = openDd.querySelectorAll(".nav-dropdown-menu a");
            if (!items.length) return;
            var focused = openDd.querySelector(".nav-dropdown-menu a:focus");
            var idx = Array.prototype.indexOf.call(items, focused);
            if (e.key === "ArrowDown") {
                idx = (idx + 1) % items.length;
            } else {
                idx = idx <= 0 ? items.length - 1 : idx - 1;
            }
            items[idx].focus();
            return;
        }

        // Enter/Space on toggle opens dropdown and focuses first item
        if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("nav-dropdown-toggle")) {
            e.preventDefault();
            var parent = e.target.closest(".nav-dropdown");
            if (parent.classList.contains("open")) {
                closeAll();
            } else {
                openDropdown(parent);
                var firstItem = parent.querySelector(".nav-dropdown-menu a");
                if (firstItem) firstItem.focus();
            }
        }
    });
});
