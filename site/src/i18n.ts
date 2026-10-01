// Interface strings for the site chrome. The manual itself is the wiki and is
// translated there; this is only what the site adds around it.

export const ui = {
  en: {
    siteTagline: "BMW CarData for Home Assistant",
    nav: { docs: "Manual", data: "Data reference", switch: "Switching from Connected Drive", install: "Install" },
    skip: "Skip to content",
    search: "Search",
    star: "Star",
    starLabel: "Star BavarianData on GitHub",
    searchPlaceholder: "Search the manual",
    theme: "Switch between light and dark",
    language: "Deutsch",
    languageLabel: "Diese Seite auf Deutsch",
    menu: "Menu",
    onThisPage: "On this page",
    contents: "Manual contents",
    previous: "Previous",
    next: "Next",
    edit: "Edit this page on GitHub",
    updated: "Updated",
    footerNote:
      "BavarianData is an independent, community-built integration and is not affiliated with, endorsed by, or sponsored by BMW Group. BMW, MINI and CarData are trademarks of their respective owners.",
    footerLinks: { source: "Source code", issues: "Report a problem", discussions: "Discussions", license: "MIT license", changelog: "What's new", support: "Support on Ko-fi", sponsor: "Sponsor on GitHub" },
    notFoundTitle: "This page doesn't exist",
    notFoundBody: "The link may be old, or the page may have moved. Start from the manual or search it.",
  },
  de: {
    siteTagline: "BMW CarData für Home Assistant",
    nav: { docs: "Handbuch", data: "Datenkatalog", switch: "Umstieg von Connected Drive", install: "Installieren" },
    skip: "Zum Inhalt springen",
    search: "Suchen",
    star: "Stern",
    starLabel: "BavarianData auf GitHub einen Stern geben",
    searchPlaceholder: "Im Handbuch suchen",
    theme: "Zwischen hell und dunkel wechseln",
    language: "English",
    languageLabel: "This page in English",
    menu: "Menü",
    onThisPage: "Auf dieser Seite",
    contents: "Inhalt des Handbuchs",
    previous: "Zurück",
    next: "Weiter",
    edit: "Diese Seite auf GitHub bearbeiten",
    updated: "Aktualisiert",
    footerNote:
      "BavarianData ist eine unabhängige Community-Integration und steht in keiner Verbindung zur BMW Group, wird von ihr weder unterstützt noch gesponsert. BMW, MINI und CarData sind Marken ihrer jeweiligen Inhaber.",
    footerLinks: { source: "Quellcode", issues: "Problem melden", discussions: "Diskussionen", license: "MIT-Lizenz", changelog: "Änderungen (EN)", support: "Auf Ko-fi unterstützen", sponsor: "Auf GitHub sponsern" },
    notFoundTitle: "Diese Seite gibt es nicht",
    notFoundBody: "Der Link ist vielleicht alt, oder die Seite ist umgezogen. Starte im Handbuch oder durchsuche es.",
  },
} as const;

export type UI = (typeof ui)["en"];
