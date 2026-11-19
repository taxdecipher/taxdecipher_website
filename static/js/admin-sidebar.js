/* Tax Decipher - shared admin sidebar behaviour.
   The collapsed/expanded choice is saved in the browser (localStorage) and
   is applied on every /root/* page, so the sidebar stays exactly as the
   user left it until the user toggles it again. */
(function () {
    var KEY = "taxDecipherSidebarCollapsed";
    var MOBILE = 900;
    var body = document.body;

    function saved() {
        try { return localStorage.getItem(KEY) === "true"; } catch (e) { return false; }
    }
    function save(value) {
        try { localStorage.setItem(KEY, String(value)); } catch (e) {}
    }

    // (The class is also set by a tiny inline script in <head> to avoid flicker.)
    if (saved()) body.classList.add("sidebar-collapsed");

    function closeMobile() { body.classList.remove("mobile-sidebar-open"); }

    var toggle = document.getElementById("sidebarToggle");
    if (toggle) {
        toggle.addEventListener("click", function () {
            if (window.innerWidth <= MOBILE) {
                body.classList.toggle("mobile-sidebar-open");
            } else {
                var collapsed = body.classList.toggle("sidebar-collapsed");
                save(collapsed);
            }
        });
    }

    // Mobile: pages such as Dashboard/Settings already have their own menu
    // button in the top bar (#mobileToggle, shown by CSS up to 900px); every
    // other page gets the shared floating button.
    var pageBtn = document.getElementById("mobileToggle");
    var floatBtn = document.getElementById("sidebarMobileBtn");

    function syncMobileButton() {
        if (!floatBtn) return;
        var isMobile = window.innerWidth <= MOBILE;
        var ownVisible = !!(pageBtn && isMobile);
        var showFloat = isMobile && !ownVisible;
        floatBtn.style.display = showFloat ? "inline-flex" : "none";
        body.classList.toggle("has-floating-menu-btn", showFloat);
    }

    function openMobile(e) {
        if (e) { e.preventDefault(); e.stopPropagation(); }
        body.classList.toggle("mobile-sidebar-open");
    }

    [pageBtn, floatBtn].forEach(function (el) {
        if (el) el.addEventListener("click", openMobile);
    });
    syncMobileButton();
    window.addEventListener("resize", syncMobileButton);

    var overlay = document.getElementById("sidebarOverlay");
    if (overlay) overlay.addEventListener("click", closeMobile);

    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") closeMobile();
    });

    document.querySelectorAll(".sidebar a").forEach(function (link) {
        link.addEventListener("click", function () {
            if (window.innerWidth <= MOBILE) closeMobile();
        });
    });

    window.addEventListener("resize", function () {
        if (window.innerWidth > MOBILE) closeMobile();
    });
})();
