/* ---------------------------------------------------------------
   site-config.js  --  Single source of truth for version strings
   Update the object below and every page picks up the change.
   --------------------------------------------------------------- */
var COHORT_VERSION = {
    extension:  "0.4.12",
    python:     "0.4.12",
    releaseTag: "v0.4.12",
    releaseUrl: "https://github.com/rwheeler007/cohort/releases/tag/v0.4.12-vscode",
    vsixFile:   "cohort-vscode-0.4.12.vsix"
};

document.addEventListener("DOMContentLoaded", function () {
    // Fill text content:  <span data-v="extension"></span>  ->  "0.4.4"
    document.querySelectorAll("[data-v]").forEach(function (el) {
        var key = el.getAttribute("data-v");
        if (COHORT_VERSION[key] != null) el.textContent = COHORT_VERSION[key];
    });

    // Fill href attributes:  <a data-v-href="releaseUrl">  ->  href="https://..."
    document.querySelectorAll("[data-v-href]").forEach(function (el) {
        var key = el.getAttribute("data-v-href");
        if (COHORT_VERSION[key] != null) el.href = COHORT_VERSION[key];
    });

    // Update JSON-LD schema blocks that contain softwareVersion
    document.querySelectorAll('script[type="application/ld+json"]').forEach(function (el) {
        try {
            var obj = JSON.parse(el.textContent);
            if (obj.softwareVersion) {
                obj.softwareVersion = COHORT_VERSION.extension;
                el.textContent = JSON.stringify(obj, null, 4);
            }
        } catch (e) { /* not valid JSON, skip */ }
    });
});
