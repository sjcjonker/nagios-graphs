/*
 * Show one-hour graph previews for allowlisted Nagios links.
 * Author: Stijn Jonker (sjcjonker) | https://github.com/sjcjonker
 * Usage: Load this script on authenticated Nagios pages with graphs.cgi action links.
 * Fetches same-origin graph images; hover displays previews and Escape hides them. No CLI help.
 * Nagios exit codes: 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN.
 */
/* Read-only one-hour graph preview for allowlisted Nagios action links. */
(() => {
  "use strict";
  const graphPath = (window.NagiosGraphs && window.NagiosGraphs.path) || "/nagios4/cgi-bin/graphs.cgi";
  const matchLink = (node) => {
    const anchor = node && node.closest ? node.closest("a[href]") : null;
    if (!anchor) return null;
    let url;
    try { url = new URL(anchor.href, location.href); } catch (_) { return null; }
    if (url.origin !== location.origin || url.pathname !== graphPath) return null;
    const view = url.searchParams.get("view");
    const serviceLink = !url.searchParams.has("view") &&
      (url.searchParams.get("host") || "").trim() &&
      (url.searchParams.get("service") || "").trim();
    // The CGI catalog is authoritative; the client checks only URL shape.
    if (!serviceLink && !/^[A-Za-z0-9_.-]{1,120}$/.test(view || "")) return null;
    return { anchor, url };
  };

  const tip = document.createElement("div");
  tip.setAttribute("role", "tooltip");
  tip.setAttribute("aria-label", "Graph, last hour");
  Object.assign(tip.style, {
    position: "fixed", zIndex: "2147483647", display: "none",
    background: "#fff", border: "1px solid #777", borderRadius: "5px",
    boxShadow: "0 5px 18px #0005", padding: "7px",
    width: "min(760px, calc(100vw - 24px))", pointerEvents: "none"
  });
  const picture = document.createElement("img");
  picture.alt = "Graph, last hour";
  Object.assign(picture.style, {display: "block", width: "100%", height: "auto"});
  tip.appendChild(picture);

  const place = (event) => {
    const width = Math.min(760, window.innerWidth - 24);
    const left = Math.max(12, Math.min(window.innerWidth - width - 12, event.clientX + 14));
    const top = Math.max(12, Math.min(window.innerHeight - (tip.offsetHeight || 320) - 12, event.clientY + 18));
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  };
  let active = null, pending = null;
  const hide = () => {
    if (pending) clearTimeout(pending);
    pending = null;
    active = null;
    tip.style.display = "none";
    picture.removeAttribute("src");
  };
  document.addEventListener("pointerover", (event) => {
    if (event.pointerType === "touch") return;
    const info = matchLink(event.target);
    if (!info || info.anchor === active) return;
    hide();
    active = info.anchor;
    const url = new URL(info.url);
    url.searchParams.set("period", "hour");
    url.searchParams.set("format", "png");
    // Refresh cached previews; all images use the same CGI renderer as the page.
    url.searchParams.set("preview", String(Math.floor(Date.now() / 60000)));
    pending = setTimeout(() => {
      pending = null;
      if (!active) return;
      picture.src = url.href;
      tip.style.display = "block";
      place(event);
    }, 200);
  });
  document.addEventListener("pointerout", (event) => {
    if (active && (event.target === active || active.contains(event.target)) &&
        (!event.relatedTarget || !active.contains(event.relatedTarget))) hide();
  });
  document.addEventListener("pointermove", (event) => {
    if (active && tip.style.display !== "none") place(event);
  }, {passive: true});
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hide();
  });
  window.addEventListener("blur", hide);
  document.addEventListener("DOMContentLoaded", () => document.body.appendChild(tip));
})();


