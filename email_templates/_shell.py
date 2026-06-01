"""Shared VisiChek email shell — the brand layer for every transactional email.

This module is the single source of truth for how VisiChek emails *look*.
It ports the marketing-site design language (visichek.app) into
email-safe, client-bulletproof HTML:

* **Brand green** ``#3A9615`` with the ``#43AA1A → #2E7A11`` button gradient
  and the ``#3A9615 → #4CBE1E`` accent bar lifted straight off the site.
* **Charcoal ink** ``#1A1A1A / #4A4A4A / #6A6A6A`` for headings, body, and
  muted text — the same neutral ramp the site uses.
* **Editorial serif headings** (Georgia, echoing the site's Moderat serif)
  paired with a clean system sans body.
* **Pill buttons**, **uppercase label tags with a green dot**, rounded cards,
  and a logo wordmark — all rebuilt as table-based, inline-styled HTML so
  they render identically in Gmail, Outlook (Word engine, via VML), Apple
  Mail, and mobile clients.

Design constraints that shaped every helper here:

* **Tables + inline styles only.** No flexbox/grid, no external CSS.
* **No remote images.** The VisiChek logomark ships only as SVG (which Gmail
  and Outlook strip), so the header is a *bulletproof HTML wordmark* — a green
  check badge + "VisiChek" — that always renders and never trips
  image-blocking. The badge echoes the brand's checkmark logomark.
* **Presentation only.** These helpers never escape or mutate caller content;
  each template stays responsible for escaping its own interpolated values
  exactly as it did before (see ``html.escape`` use in the dsr_* templates).

This is a private module (leading underscore) and is intentionally NOT a
``MountedTemplate`` — ``core/email/mounted_templates.py`` mounts templates by
explicit import, so nothing here is ever registered or sent on its own.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

# ── Brand palette (visichek.app) ─────────────────────────────────────
INK = "#1A1A1A"  # headings — deepest charcoal
INK_SOFT = "#4A4A4A"  # body copy — charcoal-light
INK_MUTED = "#6A6A6A"  # secondary notes — charcoal-lighter
INK_FAINT = "#9CA3AF"  # footer / fine print

GREEN = "#3A9615"  # primary brand green
GREEN_DARK = "#2E7A11"  # button gradient bottom / hover
GREEN_TOP = "#43AA1A"  # button gradient top
GREEN_BRIGHT = "#4CBE1E"  # accent-bar highlight
GREEN_TINT_BG = "#F0FDF4"  # green-tinted panel background
GREEN_TINT_BORDER = "#CDEBD3"  # green-tinted panel border

BORDER = "#E8E8E8"  # default hairline (site --color-border)
PAGE_BG = "#F4F6F4"  # email canvas — soft sage-neutral
CARD_BG = "#FFFFFF"
SUBTLE_BG = "#F8FBF8"  # offwhite-green inner card (credentials etc.)

AMBER_BG = "#FEF9C3"
AMBER_BORDER = "#FDE68A"
AMBER_INK = "#713F12"

DANGER_BG = "#FEF2F2"
DANGER_BORDER = "#FECACA"
DANGER_INK = "#B91C1C"

# ── Font stacks ──────────────────────────────────────────────────────
FONT_SANS = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
)
FONT_SERIF = "Georgia,'Times New Roman',Times,serif"
FONT_MONO = (
    "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"
)

# Variant → (background, border, ink) for panels and badges.
_PANEL_VARIANTS: dict[str, tuple[str, str, str]] = {
    "default": (SUBTLE_BG, BORDER, INK_SOFT),
    "brand": (GREEN_TINT_BG, GREEN_TINT_BORDER, INK_SOFT),
    "warning": (AMBER_BG, AMBER_BORDER, AMBER_INK),
    "danger": (DANGER_BG, DANGER_BORDER, DANGER_INK),
}

_BADGE_VARIANTS: dict[str, tuple[str, str]] = {
    "neutral": ("#F1F3F1", INK_MUTED),
    "brand": (GREEN_TINT_BG, GREEN_DARK),
    "warning": (AMBER_BG, AMBER_INK),
    "danger": (DANGER_BG, DANGER_INK),
}


# ── Header wordmark (bulletproof, no images) ─────────────────────────
def _wordmark() -> str:
    """A green check-badge + 'VisiChek' wordmark, pure table HTML.

    Echoes the brand's green checkmark logomark without depending on a
    remote image (the real logomark is SVG, which most inboxes strip).
    """
    return (
        "<table role='presentation' cellpadding='0' cellspacing='0' border='0'>"
        "<tr>"
        "<td width='34' height='34' align='center' valign='middle' "
        f"bgcolor='{GREEN}' style='width:34px;height:34px;border-radius:9px;"
        f"background:linear-gradient(135deg,{GREEN_TOP} 0%,{GREEN_DARK} 100%);'>"
        f"<span style='color:#FFFFFF;font-family:{FONT_SANS};font-size:19px;"
        "font-weight:700;line-height:34px;'>&#10003;</span>"
        "</td>"
        f"<td style='padding-left:11px;font-family:{FONT_SERIF};font-size:21px;"
        f"font-weight:700;color:{INK};letter-spacing:-0.01em;'>VisiChek</td>"
        "</tr></table>"
    )


def _footer() -> str:
    """Brand sign-off — tagline, copyright, NDPR line, and site links."""
    link = f"color:{INK_MUTED};text-decoration:none;"
    return (
        f"<p style='margin:0 0 8px;font-family:{FONT_SANS};font-size:12px;"
        f"line-height:1.6;color:{INK_MUTED};'>"
        "<strong style='color:#4A4A4A;'>VisiChek</strong> — visitor management "
        "for security-first workplaces.</p>"
        f"<p style='margin:0 0 10px;font-family:{FONT_SANS};font-size:12px;"
        f"line-height:1.6;color:{INK_FAINT};'>"
        "&copy; 2026 VisiChek &middot; Built in Nigeria, for Nigerians. "
        "&middot; Data security &amp; NDPR-aware by design.</p>"
        f"<p style='margin:0;font-family:{FONT_SANS};font-size:12px;"
        f"line-height:1.6;color:{INK_FAINT};'>"
        f"<a href='https://visichek.app' style='{link}'>visichek.app</a>"
        "&nbsp;&middot;&nbsp;"
        f"<a href='https://visichek.app/privacy' style='{link}'>Privacy</a>"
        "&nbsp;&middot;&nbsp;"
        f"<a href='https://visichek.app/terms' style='{link}'>Terms</a>"
        "</p>"
    )


# ── Document chrome ──────────────────────────────────────────────────
_HEAD = (
    "<!DOCTYPE html><html lang='en' "
    "xmlns='http://www.w3.org/1999/xhtml' "
    "xmlns:v='urn:schemas-microsoft-com:vml' "
    "xmlns:o='urn:schemas-microsoft-com:office:office'>"
    "<head>"
    "<meta charset='utf-8'/>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'/>"
    "<meta http-equiv='X-UA-Compatible' content='IE=edge'/>"
    "<meta name='color-scheme' content='light'/>"
    "<meta name='supported-color-schemes' content='light'/>"
    "<!--[if mso]><noscript><xml><o:OfficeDocumentSettings>"
    "<o:PixelsPerInch>96</o:PixelsPerInch>"
    "</o:OfficeDocumentSettings></xml></noscript><![endif]-->"
    "<style>"
    "a{text-decoration:none;}"
    "body{margin:0;padding:0;width:100%!important;}"
    "img{border:0;line-height:100%;outline:none;}"
    "@media only screen and (max-width:600px){"
    ".vc-card{border-radius:0!important;}"
    ".vc-pad{padding-left:24px!important;padding-right:24px!important;}"
    "}"
    "</style>"
    "</head>"
)


def page(
    content_html: str,
    *,
    preheader: str = "",
    footer_note_html: str = "",
) -> str:
    """Wrap rendered ``content_html`` in the full VisiChek email chrome.

    Args:
        content_html: the message body, assembled from the helpers below.
        preheader: hidden inbox-preview text shown next to the subject line.
        footer_note_html: optional per-email footer line (e.g.
            "Sent by Acme Corp via VisiChek.") rendered just above the
            standing brand sign-off.
    """
    preheader_block = ""
    if preheader:
        preheader_block = (
            "<div style='display:none;max-height:0;overflow:hidden;mso-hide:all;"
            f"font-size:1px;line-height:1px;color:{PAGE_BG};opacity:0;'>"
            f"{preheader}"
            "&#847;&#847;&#847;&#847;&#847;&#847;&#847;&#847;&#847;&#847;"
            "</div>"
        )

    footer_note = ""
    if footer_note_html:
        footer_note = (
            f"<p style='margin:0 0 12px;font-family:{FONT_SANS};font-size:12px;"
            f"line-height:1.6;color:{INK_MUTED};'>{footer_note_html}</p>"
        )

    return (
        _HEAD
        + f"<body style='margin:0;padding:0;background:{PAGE_BG};'>"
        + preheader_block
        + f"<table role='presentation' width='100%' cellpadding='0' "
        "cellspacing='0' border='0' "
        f"style='background:{PAGE_BG};'>"
        "<tr><td align='center' style='padding:32px 16px;'>"
        # MSO fixed-width ghost wrapper
        "<!--[if mso]><table role='presentation' width='600' cellpadding='0' "
        "cellspacing='0' border='0'><tr><td><![endif]-->"
        "<table role='presentation' class='vc-card' width='600' "
        "cellpadding='0' cellspacing='0' border='0' "
        f"style='width:600px;max-width:600px;background:{CARD_BG};"
        f"border:1px solid {BORDER};border-radius:20px;overflow:hidden;'>"
        # ── Accent bar (green gradient; solid-green fallback via bgcolor) ──
        f"<tr><td height='4' bgcolor='{GREEN}' "
        f"style='height:4px;line-height:4px;font-size:0;"
        f"background:linear-gradient(90deg,{GREEN} 0%,{GREEN_BRIGHT} 50%,"
        f"{GREEN} 100%);'>&nbsp;</td></tr>"
        # ── Header ──
        "<tr><td class='vc-pad' style='padding:30px 36px 6px;'>"
        + _wordmark()
        + "</td></tr>"
        # ── Body ──
        "<tr><td class='vc-pad' style='padding:14px 36px 28px;'>"
        + content_html
        + "</td></tr>"
        # ── Footer ──
        "<tr><td class='vc-pad' "
        f"style='padding:22px 36px 30px;border-top:1px solid {BORDER};'>"
        + footer_note
        + _footer()
        + "</td></tr>"
        "</table>"
        "<!--[if mso]></td></tr></table><![endif]-->"
        "</td></tr></table></body></html>"
    )


# ── Content helpers ──────────────────────────────────────────────────
def eyebrow(text: str, *, variant: str = "brand") -> str:
    """Uppercase label tag with a leading dot — the site's ``.label-tag``."""
    dot, ink = {
        "brand": (GREEN, GREEN_DARK),
        "neutral": (INK_FAINT, INK_MUTED),
        "danger": (DANGER_INK, DANGER_INK),
    }.get(variant, (GREEN, GREEN_DARK))
    return (
        f"<p style='margin:0 0 12px;font-family:{FONT_SANS};font-size:11px;"
        f"font-weight:700;letter-spacing:0.12em;text-transform:uppercase;"
        f"color:{ink};'>"
        f"<span style='display:inline-block;width:6px;height:6px;border-radius:9999px;"
        f"background:{dot};vertical-align:middle;margin-right:7px;'></span>"
        f"{text}</p>"
    )


def heading(text: str) -> str:
    """Editorial serif H1 — echoes the site's Moderat-serif headings."""
    return (
        f"<h1 style='margin:0 0 14px;font-family:{FONT_SERIF};font-size:25px;"
        f"line-height:1.25;font-weight:700;color:{INK};letter-spacing:-0.02em;'>"
        f"{text}</h1>"
    )


def paragraph(inner_html: str) -> str:
    """Standard body paragraph."""
    return (
        f"<p style='margin:0 0 16px;font-family:{FONT_SANS};font-size:15px;"
        f"line-height:1.62;color:{INK_SOFT};'>{inner_html}</p>"
    )


def muted(inner_html: str) -> str:
    """Small, secondary note (security reminders, fine print)."""
    return (
        f"<p style='margin:0 0 14px;font-family:{FONT_SANS};font-size:13px;"
        f"line-height:1.6;color:{INK_MUTED};'>{inner_html}</p>"
    )


def button(label: str, href: str) -> str:
    """Bulletproof green pill CTA. Returns ``""`` when ``href`` is empty."""
    if not href:
        return ""
    return (
        "<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
        "style='margin:8px 0 20px;'><tr>"
        f"<td align='center' bgcolor='{GREEN}' "
        f"style='border-radius:9999px;"
        f"background:linear-gradient(180deg,{GREEN_TOP} 0%,{GREEN_DARK} 100%);'>"
        "<!--[if mso]>"
        "<v:roundrect xmlns:v='urn:schemas-microsoft-com:vml' "
        "xmlns:w='urn:schemas-microsoft-com:office:word' "
        f"href='{href}' style='height:48px;v-text-anchor:middle;width:260px;' "
        f"arcsize='50%' strokecolor='{GREEN_DARK}' fillcolor='{GREEN}'>"
        "<w:anchorlock/>"
        "<center style='color:#FFFFFF;font-family:sans-serif;font-size:15px;"
        f"font-weight:bold;'>{label}</center>"
        "</v:roundrect>"
        "<![endif]-->"
        "<!--[if !mso]><!-->"
        f"<a href='{href}' style='display:inline-block;padding:14px 32px;"
        f"font-family:{FONT_SANS};font-size:15px;font-weight:600;color:#FFFFFF;"
        "text-decoration:none;border-radius:9999px;'>"
        f"{label}</a>"
        "<!--<![endif]-->"
        "</td></tr></table>"
    )


def fallback_link(
    url: str,
    *,
    intro: str = "If the button doesn't work, paste this link into your browser:",
) -> str:
    """Plain-URL fallback for clients that strip or mis-render the button."""
    if not url:
        return ""
    return (
        f"<p style='margin:0 0 8px;font-family:{FONT_SANS};font-size:13px;"
        f"line-height:1.55;color:{INK_MUTED};'>{intro}</p>"
        f"<p style='margin:0 0 18px;font-family:{FONT_MONO};font-size:12px;"
        f"line-height:1.5;color:{GREEN_DARK};word-break:break-all;'>{url}</p>"
    )


def cred_card(rows: Sequence[tuple[str, str, bool]]) -> str:
    """Credentials panel: list of ``(label, value, is_mono)`` rows.

    Used for sign-in email + temporary password. ``is_mono`` renders the
    value in the monospace stack (passwords, codes).
    """
    cells = []
    for i, (label, value, is_mono) in enumerate(rows):
        spacing = "margin:0;" if i == len(rows) - 1 else "margin:0 0 14px;"
        if is_mono:
            value_html = (
                f"<p style='{spacing}font-family:{FONT_MONO};font-size:15px;"
                f"font-weight:700;color:{INK};letter-spacing:0.01em;'>{value}</p>"
            )
        else:
            value_html = (
                f"<p style='{spacing}font-family:{FONT_SANS};font-size:15px;"
                f"font-weight:600;color:{INK};'>{value}</p>"
            )
        cells.append(
            f"<p style='margin:0 0 5px;font-family:{FONT_SANS};font-size:12px;"
            f"text-transform:uppercase;letter-spacing:0.06em;color:{INK_MUTED};'>"
            f"{label}</p>" + value_html
        )
    return (
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        f"border='0' style='margin:4px 0 20px;background:{SUBTLE_BG};"
        f"border:1px solid {BORDER};border-radius:14px;'>"
        f"<tr><td style='padding:18px 22px;'>{''.join(cells)}</td></tr></table>"
    )


def code_box(code: str, *, label: str = "Verification code") -> str:
    """Prominent one-time-code panel (OTP) — green-tinted, large mono code."""
    return (
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        f"border='0' style='margin:6px 0 20px;background:{GREEN_TINT_BG};"
        f"border:1px solid {GREEN_TINT_BORDER};border-radius:14px;'>"
        "<tr><td align='center' style='padding:22px 24px;'>"
        f"<p style='margin:0 0 8px;font-family:{FONT_SANS};font-size:11px;"
        f"font-weight:700;text-transform:uppercase;letter-spacing:0.12em;"
        f"color:{GREEN_DARK};'>{label}</p>"
        f"<p style='margin:0;font-family:{FONT_MONO};font-size:34px;"
        f"font-weight:700;letter-spacing:0.18em;color:{INK};'>{code}</p>"
        "</td></tr></table>"
    )


def _list(items: Iterable[str], *, ordered: bool) -> str:
    tag = "ol" if ordered else "ul"
    lis = "".join(
        f"<li style='margin:0 0 7px;'>{item}</li>" for item in items
    )
    return (
        f"<{tag} style='margin:0 0 18px;padding-left:20px;font-family:{FONT_SANS};"
        f"font-size:15px;line-height:1.6;color:{INK_SOFT};'>{lis}</{tag}>"
    )


def ordered_steps(items: Iterable[str]) -> str:
    """Numbered list — first-sign-in steps, what-to-do-next, etc."""
    return _list(items, ordered=True)


def bullet_list(items: Iterable[str]) -> str:
    """Bulleted list — pending fields, case detail rows, etc."""
    return _list(items, ordered=False)


def panel(inner_html: str, *, variant: str = "default", title: str | None = None) -> str:
    """Soft callout card. Variants: default / brand / warning / danger."""
    bg, border, ink = _PANEL_VARIANTS.get(variant, _PANEL_VARIANTS["default"])
    title_html = ""
    if title:
        title_html = (
            f"<p style='margin:0 0 7px;font-family:{FONT_SANS};font-size:14px;"
            f"font-weight:700;color:{ink};'>{title}</p>"
        )
    return (
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        f"border='0' style='margin:4px 0 18px;background:{bg};"
        f"border:1px solid {border};border-radius:14px;'>"
        f"<tr><td style='padding:14px 20px;font-family:{FONT_SANS};font-size:14px;"
        f"line-height:1.6;color:{ink};'>{title_html}{inner_html}</td></tr></table>"
    )


def quote(inner_html: str) -> str:
    """Message-preview blockquote with a green left rule."""
    return (
        f"<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        f"border='0' style='margin:4px 0 18px;'>"
        f"<tr><td style='padding:12px 18px;background:{SUBTLE_BG};"
        f"border-left:3px solid {GREEN};border-radius:0 10px 10px 0;"
        f"font-family:{FONT_SANS};font-size:14px;line-height:1.6;"
        f"font-style:italic;color:{INK_SOFT};'>{inner_html}</td></tr></table>"
    )


def meta_rows(pairs: Sequence[tuple[str, str]]) -> str:
    """Compact ``Label: value`` stack (host, department, etc.). Empty -> ""."""
    rows = [
        f"<p style='margin:0 0 6px;font-family:{FONT_SANS};font-size:14px;"
        f"line-height:1.5;color:{INK_SOFT};'>"
        f"<span style='color:{INK_MUTED};'>{label}:</span> "
        f"<strong style='color:{INK};'>{value}</strong></p>"
        for label, value in pairs
        if value
    ]
    if not rows:
        return ""
    return f"<div style='margin:0 0 16px;'>{''.join(rows)}</div>"


def badge(text: str, *, variant: str = "neutral") -> str:
    """Small status/priority pill."""
    bg, ink = _BADGE_VARIANTS.get(variant, _BADGE_VARIANTS["neutral"])
    return (
        f"<span style='display:inline-block;padding:3px 11px;border-radius:9999px;"
        f"background:{bg};font-family:{FONT_SANS};font-size:11px;font-weight:700;"
        f"letter-spacing:0.04em;text-transform:uppercase;color:{ink};'>{text}</span>"
    )


def code_chip(text: str) -> str:
    """Inline monospace chip (case IDs, reception fallback codes)."""
    return (
        f"<span style='font-family:{FONT_MONO};font-size:12px;background:{SUBTLE_BG};"
        f"border:1px solid {BORDER};padding:2px 7px;border-radius:5px;"
        f"color:{INK};'>{text}</span>"
    )


def divider() -> str:
    """Hairline rule, for separating sections inside the body."""
    return (
        f"<div style='height:1px;line-height:1px;font-size:0;background:{BORDER};"
        "margin:4px 0 18px;'>&nbsp;</div>"
    )


# ── Plain-text sign-off ──────────────────────────────────────────────
def text_signoff(*, sender_label: str | None = None) -> list[str]:
    """Standard footer lines for the plain-text alternative part."""
    lines = ["", "—"]
    if sender_label:
        lines.append(sender_label)
    lines.append("VisiChek — visitor management for security-first workplaces.")
    lines.append("© 2026 VisiChek · visichek.app")
    return lines
