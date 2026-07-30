"""Gradio UI for Job Scout.

A four-step wizard with a refined editorial look: warm-paper background with a
faint gradient-and-grain wash, a display serif (Fraunces) paired with a mono for
data (IBM Plex Mono), emerald accent. The flow is (1) drop your resume,
(2) review the extracted profile and choose where to search (the human decides
locations and remote, not the model), (3) find jobs — ranked as cards with a
conic-gauge fit score, matched-skill chips, and honest gaps — and (4) tailor an
application for a selected job: cover letter + reworded CV, every claim checked
against the resume, downloadable as PDF/.tex.

When Jobvis is configured (see job_scout.voice), a voice strip sits above the
wizard: a tap-to-talk session that narrates results and can trigger runs, whose
finished work the 1s Timer pushes into the step pages.
"""

from __future__ import annotations

import tempfile
from html import escape
from pathlib import Path
from uuid import uuid4

import gradio as gr

from job_scout import candidate_store
from job_scout.graph.schemas import Profile, RankedJob
from job_scout.profile import extract_profile
from job_scout.renderer import render_pdf
from job_scout.runner import RunResult, TailorResult, stream_search, stream_tailor
from job_scout.tools.cv_reader import CVReadError, extract_cv_text
from job_scout.tracing import opik_url, register_prompts
from job_scout.voice import bridge as voice_bridge
from job_scout.voice import is_voice_available

CAPTION = "Prepares applications — never submits them."

INTRO_HTML = """
<p class="js-lead">Drop your resume and let Job Scout find roles that actually match it —
ranked by fit, with the gaps shown honestly.</p>
<ol class="js-steps">
  <li><span class="js-num">1</span><div><b>Drop your resume</b> (PDF) below.</div></li>
  <li><span class="js-num">2</span><div>We <b>read it</b> and show your skills, experience, and locations.</div></li>
  <li><span class="js-num">3</span><div>Hit <b>Find jobs</b> to get openings ranked by fit.</div></li>
</ol>
"""

THEME = gr.themes.Soft(
    primary_hue=gr.themes.colors.emerald,
    secondary_hue=gr.themes.colors.stone,
    neutral_hue=gr.themes.colors.stone,
    font=(gr.themes.GoogleFont("Public Sans"), "ui-sans-serif", "system-ui", "sans-serif"),
    radius_size=gr.themes.sizes.radius_md,
).set(
    body_background_fill="#FBFAF8",
    body_background_fill_dark="#141311",
    block_background_fill="#FFFFFF",
    block_background_fill_dark="#1E1D1A",
    button_primary_background_fill="#0E6E4A",
    button_primary_background_fill_hover="#0A5539",
    button_primary_text_color="#FFFFFF",
    button_large_radius="12px",
)

# Fine tiled grain, kept as a constant so the CSS block stays within line length.
_GRAIN = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.5'/%3E%3C/svg%3E\")"  # noqa: E501

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600;9..144,700&family=Public+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@500;600&display=swap');

:root {
  --js-accent: #0E6E4A;
  --js-accent-bright: #0E9C68;
  --js-ring-track: rgba(20,19,17,0.08);
}
.dark {
  --js-ring-track: rgba(255,255,255,0.10);
}

/* --- Atmospheric background: faint emerald + amber wash over warm paper --- */
.gradio-container {
  max-width: 760px !important;
  margin: 0 auto !important;
  position: relative;
}
body::before {
  content: "";
  position: fixed; inset: 0; z-index: 0; pointer-events: none;
  background:
    radial-gradient(60% 45% at 12% 4%, rgba(14,156,104,0.10), transparent 70%),
    radial-gradient(55% 45% at 92% 8%, rgba(180,83,9,0.07), transparent 72%);
}
.dark body::before {
  background:
    radial-gradient(60% 45% at 12% 4%, rgba(14,156,104,0.14), transparent 70%),
    radial-gradient(55% 45% at 92% 8%, rgba(180,83,9,0.10), transparent 72%);
}
/* fine grain */
body::after {
  content: "";
  position: fixed; inset: 0; z-index: 0; pointer-events: none; opacity: 0.35;
  background-image: __GRAIN__;
}
.dark body::after { opacity: 0.5; mix-blend-mode: screen; }
.gradio-container > * { position: relative; z-index: 1; }

/* --- Header --- */
#js-header { text-align: center; padding: 22px 0 4px; }
#js-header .js-mark {
  display: inline-flex; align-items: center; justify-content: center; gap: 10px;
}
#js-header .js-mark svg { width: 30px; height: 30px; color: var(--js-accent); }
#js-header h1 {
  font-family: 'Fraunces', Georgia, serif; font-weight: 600;
  font-size: 2.85rem; letter-spacing: -0.025em; margin: 0; line-height: 1.02;
}
#js-header .js-tag {
  display: inline-block; margin-top: 13px; font-size: 0.76rem; letter-spacing: 0.01em;
  color: var(--body-text-color-subdued);
  border: 1px solid var(--border-color-primary); border-radius: 999px; padding: 4px 14px;
  background: color-mix(in srgb, var(--block-background-fill) 55%, transparent);
  backdrop-filter: blur(4px);
}

/* --- Stepper --- */
.js-stepper {
  list-style: none; display: flex; align-items: center; justify-content: center;
  gap: 0; margin: 20px auto 6px; padding: 0; max-width: 420px;
}
.js-step { display: flex; align-items: center; gap: 9px; font-size: 0.82rem; }
.js-step-dot {
  width: 26px; height: 26px; border-radius: 50%; flex: none;
  display: grid; place-items: center; font-size: 0.78rem; font-weight: 700;
  font-family: 'IBM Plex Mono', monospace;
  border: 1.5px solid var(--border-color-primary); color: var(--body-text-color-subdued);
  background: var(--block-background-fill); transition: all .2s ease;
}
.js-step-label { color: var(--body-text-color-subdued); font-weight: 500; }
.js-step::after {
  content: ""; width: 34px; height: 1.5px; margin: 0 12px;
  background: var(--border-color-primary); border-radius: 2px;
}
.js-step:last-child::after { display: none; }
.js-step-active .js-step-dot {
  border-color: var(--js-accent); color: #fff; background: var(--js-accent);
  box-shadow: 0 0 0 4px rgba(14,110,74,0.14);
}
.js-step-active .js-step-label { color: var(--body-text-color); font-weight: 600; }
.js-step-done .js-step-dot {
  border-color: var(--js-accent); color: var(--js-accent);
  background: rgba(14,110,74,0.12);
}

/* --- Intro --- */
.js-lead { text-align: center; font-size: 1.05rem; line-height: 1.55; color: var(--body-text-color-subdued);
  margin: 14px auto 4px; max-width: 480px; }
.js-steps { list-style: none; padding: 0; margin: 18px auto 8px; max-width: 470px; }
.js-steps li { display: flex; gap: 13px; align-items: flex-start; padding: 9px 0; font-size: 0.95rem; line-height: 1.45; }
.js-steps .js-num { flex: none; width: 26px; height: 26px; border-radius: 50%;
  background: rgba(14,110,74,0.12); color: var(--js-accent); font-weight: 700; font-size: 0.82rem;
  font-family: 'IBM Plex Mono', monospace; margin-top: 1px;
  display: flex; align-items: center; justify-content: center; }

.js-section-label { font-size: 0.76rem; font-weight: 600; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--body-text-color-subdued); margin: 4px 0 10px 2px; }
.js-muted { color: var(--body-text-color-subdued); }

/* --- Dropzone polish --- */
.js-drop { border: 1.5px dashed var(--border-color-primary) !important;
  border-radius: 16px !important; background: color-mix(in srgb, var(--block-background-fill) 70%, transparent) !important;
  transition: border-color .2s ease, background .2s ease; }
.js-drop:hover { border-color: var(--js-accent) !important; }

/* --- Cards --- */
.js-card { background: var(--block-background-fill); border: 1px solid var(--border-color-primary);
  border-radius: 16px; padding: 20px 22px; box-shadow: 0 1px 2px rgba(20,19,17,0.03); }
.js-profile-name { font-family: 'Fraunces', serif; font-size: 1.5rem; font-weight: 600; margin: 0; letter-spacing: -0.01em; }
.js-profile-sub { color: var(--body-text-color-subdued); font-size: 0.92rem; margin: 3px 0 14px; }
.js-profile-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 4px 0 14px;
  border-top: 1px solid var(--border-color-primary); padding-top: 14px; }
.js-stat .js-stat-val { font-family: 'IBM Plex Mono', monospace; font-size: 1.15rem; font-weight: 600; line-height: 1; }
.js-stat .js-stat-key { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--body-text-color-subdued); margin-top: 5px; }
.js-profile-row { font-size: 0.9rem; margin: 3px 0; }
.js-profile-row b { font-weight: 600; }
.js-pill { display: inline-block; font-size: 0.75rem; padding: 3px 11px; border-radius: 999px;
  background: rgba(14,110,74,0.10); color: var(--js-accent); margin: 5px 5px 0 0; }

/* --- Job cards --- */
.js-jobs { display: flex; flex-direction: column; gap: 12px; }
.js-job { border: 1px solid var(--border-color-primary); border-radius: 14px;
  padding: 17px 19px; background: var(--block-background-fill);
  box-shadow: 0 1px 2px rgba(20,19,17,0.03);
  transition: box-shadow .18s ease, transform .18s ease, border-color .18s ease;
  opacity: 0; transform: translateY(8px); animation: js-rise .4s ease forwards; }
.js-job:hover { box-shadow: 0 12px 30px rgba(20,19,17,0.09); transform: translateY(-2px);
  border-color: color-mix(in srgb, var(--js-accent) 40%, var(--border-color-primary)); }
@keyframes js-rise { to { opacity: 1; transform: none; } }

.js-job-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.js-job-title { font-weight: 600; font-size: 1.06rem; text-decoration: none; color: var(--body-text-color);
  line-height: 1.3; }
.js-job-title:hover { color: var(--js-accent); }
.js-job-meta { font-size: 0.85rem; color: var(--body-text-color-subdued); margin-top: 4px;
  display: flex; align-items: center; flex-wrap: wrap; gap: 7px; }
.js-remote, .js-source { font-size: 0.66rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
  border-radius: 5px; padding: 2px 7px; line-height: 1.5; }
.js-remote { color: var(--js-accent); border: 1px solid rgba(14,110,74,0.35); }
.js-source { color: var(--body-text-color-subdued); border: 1px solid var(--border-color-primary); }
.js-job-why { font-size: 0.92rem; line-height: 1.55; margin: 12px 0 0; }

/* chips */
.js-chips { margin-top: 11px; display: flex; flex-wrap: wrap; gap: 6px; }
.js-chip { display: inline-flex; align-items: center; gap: 4px; font-size: 0.75rem;
  padding: 3px 10px; border-radius: 999px; line-height: 1.5; }
.js-chip-match { background: rgba(14,110,74,0.11); color: var(--js-accent); }
.js-chip-gap { background: rgba(100,116,139,0.13); color: var(--body-text-color-subdued); }

/* --- Fit gauge --- */
.js-fit-ring { --size: 56px; width: var(--size); height: var(--size); flex: none;
  border-radius: 50%; display: grid; place-items: center; position: relative;
  background: conic-gradient(var(--ring) calc(var(--val) * 1%), var(--js-ring-track) 0); }
.js-fit-ring::before { content: ""; position: absolute; inset: 5px; border-radius: 50%;
  background: var(--block-background-fill); }
.js-fit-num { position: relative; font-family: 'IBM Plex Mono', monospace; font-weight: 600;
  font-size: 1.05rem; color: var(--tier-fg); }
.js-fit-high { --ring: var(--js-accent-bright); --tier-fg: #0E6E4A; }
.js-fit-mid  { --ring: #D97706; --tier-fg: #B45309; }
.js-fit-low  { --ring: #94A3B8; --tier-fg: #64748B; }
.dark .js-fit-high { --tier-fg: #34D399; }
.dark .js-fit-mid  { --tier-fg: #FBBF24; }
.dark .js-fit-low  { --tier-fg: #94A3B8; }

/* --- States --- */
.js-empty { text-align: center; padding: 40px 20px; color: var(--body-text-color-subdued); }
.js-empty .js-empty-icon { font-size: 1.8rem; opacity: 0.8; margin-bottom: 8px; }
.js-status { font-size: 0.86rem; color: var(--body-text-color-subdued); text-align: center; min-height: 18px; }
.js-status-err { color: #B45309; }
.js-spin { display: inline-block; width: 13px; height: 13px; margin-right: 8px; vertical-align: -1px;
  border: 2px solid rgba(14,110,74,0.25); border-top-color: var(--js-accent); border-radius: 50%;
  animation: js-rotate .7s linear infinite; }
@keyframes js-rotate { to { transform: rotate(360deg); } }

/* --- Footer --- */
.js-footer { text-align: center; font-size: 0.82rem; color: var(--body-text-color-subdued);
  border-top: 1px solid var(--border-color-primary); margin-top: 20px; padding-top: 15px; }
.js-footer .js-meta-mono { font-family: 'IBM Plex Mono', monospace; }
.js-footer a { color: var(--js-accent); text-decoration: none; }
.js-footer a:hover { text-decoration: underline; }

/* --- Tailor pack (Phase 2) --- */
.js-pack { display: flex; flex-direction: column; gap: 14px; }
.js-letter { white-space: pre-wrap; font-size: 0.93rem; line-height: 1.6; }
.js-cvprev-role { font-weight: 600; margin: 10px 0 2px; }
.js-cvprev-dates { color: var(--body-text-color-subdued); font-size: 0.85rem; font-weight: 400; }
.js-cvprev ul { margin: 4px 0 0; padding-left: 1.2em; }
.js-cvprev li { font-size: 0.9rem; line-height: 1.5; margin: 3px 0; }
.js-ref { font-family: 'IBM Plex Mono', monospace; font-size: 0.66rem; color: var(--js-accent);
  background: rgba(14,110,74,0.09); border-radius: 5px; padding: 1px 6px; margin-left: 6px;
  vertical-align: 1px; white-space: nowrap; }
.js-honesty { border-left: 3px solid #D97706; background: rgba(217,119,6,0.07);
  border-radius: 0 12px 12px 0; padding: 12px 16px; font-size: 0.9rem; line-height: 1.55; }
.js-honesty b { color: #B45309; }
.js-fab-ok { border: 1px solid rgba(14,110,74,0.35); background: rgba(14,110,74,0.06);
  border-radius: 12px; padding: 11px 16px; font-size: 0.88rem; color: var(--js-accent); }
.js-fab-warn { border: 1px solid rgba(217,119,6,0.45); background: rgba(217,119,6,0.07);
  border-radius: 12px; padding: 12px 16px; font-size: 0.88rem; }
.js-fab-warn b { color: #B45309; }
.js-fab-warn ul { margin: 8px 0 0; padding-left: 1.2em; }
.js-fab-warn li { margin: 4px 0; line-height: 1.5; }
.js-fab-warn .js-fab-reason { color: var(--body-text-color-subdued); font-size: 0.8rem; }

/* --- Strip the Gradio chrome: pages float directly on the paper --- */
footer { display: none !important; }
.js-page { background: transparent !important; border: none !important; box-shadow: none !important; }
.js-page > .styler, .js-page .form { background: transparent !important; border: none !important; box-shadow: none !important; }

/* --- Jobvis voice strip: a dark console over the paper --- */
#jv-strip { background: linear-gradient(140deg, #1A241F 0%, #101713 100%) !important;
  border: 1px solid rgba(14,156,104,0.28) !important; border-radius: 18px !important;
  padding: 14px 18px !important; box-shadow: 0 14px 40px rgba(10, 20, 16, 0.28); }
#jv-strip .styler, #jv-strip .form, #jv-strip .block { background: transparent !important; border: none !important;
  box-shadow: none !important; }
.jv-row { align-items: center !important; }
#jv-strip button { background: rgba(14,156,104,0.14) !important; color: #7DE3B5 !important;
  border: 1px solid rgba(14,156,104,0.45) !important; border-radius: 999px !important;
  font-weight: 600; letter-spacing: 0.01em; transition: all .2s ease; white-space: nowrap; }
#jv-strip button:hover { background: rgba(14,156,104,0.26) !important; border-color: rgba(125,227,181,0.7) !important; }
#jv-strip .jv-status { display: flex; align-items: center; min-height: 34px;
  font-family: 'IBM Plex Mono', monospace; font-size: 0.76rem; letter-spacing: 0.02em;
  color: rgba(236,242,238,0.72); }
.jv-dot { width: 12px; height: 12px; border-radius: 50%; flex: none; margin-right: 10px;
  background: radial-gradient(circle at 35% 30%, rgba(255,255,255,0.35), rgba(120,130,125,0.6));
  display: inline-block; }
.jv-active .jv-dot { background: radial-gradient(circle at 35% 30%, #B9F5DB, var(--js-accent-bright));
  animation: jv-pulse 1.6s ease infinite;
  transform: scale(calc(1 + var(--jv-level, 0) * 1.1));
  filter: brightness(calc(1 + var(--jv-level, 0) * 0.8));
  transition: transform .45s ease, filter .45s ease; }
.jv-lat { opacity: 0.55; margin-left: 8px; }
.jv-error .jv-dot { background: radial-gradient(circle at 35% 30%, #FBD9A5, #B45309); }
@keyframes jv-pulse { 0%, 100% { box-shadow: 0 0 0 0 rgba(14,156,104,0.45); }
  50% { box-shadow: 0 0 0 9px rgba(14,156,104,0); } }
#jv-strip .label-wrap span { color: rgba(236,242,238,0.6); font-size: 0.78rem;
  font-family: 'IBM Plex Mono', monospace; text-transform: uppercase; letter-spacing: 0.08em; }
.jv-hint { text-align: center; font-size: 0.78rem; color: var(--body-text-color-subdued); margin: 6px 0 0; }
.jv-link { font-family: 'IBM Plex Mono', monospace; font-size: 0.76rem; color: #7DE3B5 !important;
  text-decoration: none; white-space: nowrap; align-self: center; }
.jv-link:hover { text-decoration: underline; }
.jv-transcript { font-size: 0.82rem; line-height: 1.65; max-height: 230px; overflow-y: auto;
  background: rgba(0,0,0,0.28); border: 1px solid rgba(255,255,255,0.06); border-radius: 12px;
  padding: 12px 14px; color: rgba(236,242,238,0.88); }
.jv-transcript b { color: #B9F5DB; font-weight: 600; }
.jv-transcript .jv-tool { font-family: 'IBM Plex Mono', monospace; font-size: 0.7rem; color: #66D9A8;
  opacity: 0.9; margin: 2px 0; }
.jv-transcript .jv-system { color: rgba(236,242,238,0.45); font-style: italic; }

/* --- Jarvis mode (/jarvis): full-dark voice console ------------------------ */
#jx-backdrop { position: fixed; inset: 0; z-index: 1; pointer-events: none;
  background: #0C110F; }
#jx-backdrop::after { content: ""; position: absolute; inset: 0;
  background: radial-gradient(55% 40% at 50% 18%, rgba(14,156,104,0.16), transparent 70%),
              radial-gradient(70% 50% at 50% 100%, rgba(10,60,42,0.25), transparent 70%); }
#jx-root { position: relative; z-index: 2; padding: 26px 0 40px; text-align: center; }
#jx-root .jx-title { font-family: 'Fraunces', Georgia, serif; font-weight: 600; font-size: 2rem;
  color: #ECF2EE; letter-spacing: -0.02em; margin: 0 0 4px; }
#jx-root .jx-sub { font-family: 'IBM Plex Mono', monospace; font-size: 0.72rem; letter-spacing: 0.14em;
  text-transform: uppercase; color: rgba(236,242,238,0.45); margin: 0 0 26px; }
.jx-orb-wrap { display: flex; flex-direction: column; align-items: center; gap: 16px; margin: 6px 0 18px; }
.jx-orb { width: 132px; height: 132px; border-radius: 50%;
  background: radial-gradient(circle at 38% 32%, rgba(185,245,219,0.9), rgba(14,156,104,0.55) 46%, rgba(10,40,30,0.9) 78%);
  box-shadow: 0 0 40px rgba(14,156,104,0.25), inset 0 0 30px rgba(0,0,0,0.35);
  opacity: 0.45; transition: transform .45s ease, box-shadow .45s ease, opacity .45s ease; }
.jx-live .jx-orb { opacity: 1;
  transform: scale(calc(1 + var(--jv-level, 0) * 0.35));
  box-shadow: 0 0 calc(44px + var(--jv-level, 0) * 90px) rgba(14,200,130, calc(0.30 + var(--jv-level, 0) * 0.45)),
              inset 0 0 30px rgba(0,0,0,0.3);
  animation: jx-breathe 3.2s ease-in-out infinite; }
.jx-err .jx-orb { background: radial-gradient(circle at 38% 32%, #FBD9A5, #B45309 60%, #3a1e04); opacity: 0.9; }
@keyframes jx-breathe { 0%, 100% { filter: brightness(1); } 50% { filter: brightness(1.18); } }
.jx-orb-status { font-family: 'IBM Plex Mono', monospace; font-size: 0.8rem; color: rgba(236,242,238,0.7); }
#jx-root button { border-radius: 999px !important; }
.jx-panel { background: rgba(255,255,255,0.045); border: 1px solid rgba(14,156,104,0.25);
  border-radius: 16px; padding: 16px 20px; margin: 14px auto; max-width: 560px; text-align: left;
  color: rgba(236,242,238,0.88); font-size: 0.92rem; line-height: 1.55; }
.jx-panel-title { font-family: 'IBM Plex Mono', monospace; font-size: 0.7rem; letter-spacing: 0.12em;
  text-transform: uppercase; color: #66D9A8; margin: 0 0 10px; }
.jx-row { display: flex; align-items: center; gap: 14px; padding: 8px 0;
  border-top: 1px solid rgba(255,255,255,0.06); }
.jx-row:first-of-type { border-top: none; }
.jx-row .jx-rank { font-family: 'IBM Plex Mono', monospace; color: #66D9A8; font-size: 0.8rem; flex: none; }
.jx-row .jx-job { flex: 1; }
.jx-row .jx-job b { color: #ECF2EE; }
.jx-row .jx-meta { font-size: 0.8rem; color: rgba(236,242,238,0.55); }
.jx-score { font-family: 'IBM Plex Mono', monospace; font-weight: 600; font-size: 1.05rem; color: #7DE3B5; flex: none; }
.jx-empty { color: rgba(236,242,238,0.55); font-size: 0.9rem; }
#jx-root .jv-transcript { max-width: 560px; margin: 0 auto; text-align: left; }

/* accessibility: visible focus */
a:focus-visible, button:focus-visible, .js-job-title:focus-visible {
  outline: 2px solid var(--js-accent); outline-offset: 2px; border-radius: 4px; }

@media (max-width: 520px) {
  #js-header h1 { font-size: 2.3rem; }
  .js-step-label { display: none; }
  .js-step::after { width: 22px; margin: 0 8px; }
  .js-profile-grid { grid-template-columns: repeat(3, 1fr); gap: 8px; }
}
@media (prefers-reduced-motion: reduce) {
  .js-job { animation: none; opacity: 1; transform: none; }
  .js-spin { animation: none; }
  .jv-active .jv-dot { animation: none; }
}
""".replace("__GRAIN__", _GRAIN)

# Small compass mark for the wordmark.
_MARK = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2.1 5-5 2.1 2.1-5z"/></svg>'
)


def _stepper(active: int) -> str:
    """Render the 4-step progress indicator with ``active`` (1-4) highlighted."""
    labels = ("Resume", "Profile", "Jobs", "Tailor")
    items = []
    for i, label in enumerate(labels, 1):
        state = "done" if i < active else "active" if i == active else "todo"
        mark = "✓" if state == "done" else str(i)
        items.append(
            f'<li class="js-step js-step-{state}">'
            f'<span class="js-step-dot">{mark}</span>'
            f'<span class="js-step-label">{label}</span></li>'
        )
    return f'<ol class="js-stepper">{"".join(items)}</ol>'


def _loading_html(text: str) -> str:
    """Centered spinner with a message."""
    return f'<div class="js-empty"><div class="js-empty-icon"><span class="js-spin"></span></div><div>{escape(text)}</div></div>'


def _chips(items: list[str], kind: str, limit: int) -> str:
    """Render a row of matched-skill or gap chips."""
    icon = "✓ " if kind == "match" else ""
    shown = "".join(f'<span class="js-chip js-chip-{kind}">{icon}{escape(s)}</span>' for s in items[:limit])
    return f'<div class="js-chips">{shown}</div>' if shown else ""


def _profile_html(profile: Profile | None) -> str:
    """Render the extracted profile as a card."""
    if profile is None:
        return '<div class="js-card js-muted">Could not read a profile from that resume.</div>'
    skills = "".join(f'<span class="js-pill">{escape(s)}</span>' for s in profile.skills) or "—"
    years = f"{profile.years_experience:g}" if profile.years_experience else "—"
    languages = escape(", ".join(profile.languages) or "—")
    return (
        '<div class="js-card">'
        f'<p class="js-profile-name">{escape(profile.name or "Candidate")}</p>'
        f'<p class="js-profile-sub">{escape(profile.seniority.title())} · '
        f"{escape(', '.join(profile.primary_roles) or '—')}</p>"
        '<div class="js-profile-grid">'
        f'<div class="js-stat"><div class="js-stat-val">{years}</div><div class="js-stat-key">Years exp</div></div>'
        f'<div class="js-stat"><div class="js-stat-val">{"Yes" if profile.remote_ok else "No"}</div>'
        '<div class="js-stat-key">Remote</div></div>'
        f'<div class="js-stat"><div class="js-stat-val">{len(profile.skills)}</div><div class="js-stat-key">Skills</div></div>'
        "</div>"
        f'<p class="js-profile-row"><b>Locations</b> &nbsp;{escape(", ".join(profile.locations) or "—")}</p>'
        f'<p class="js-profile-row"><b>Languages</b> &nbsp;{languages}</p>'
        f'<div style="margin-top:12px">{skills}</div>'
        "</div>"
    )


def _fit_class(score: int) -> str:
    """Return the CSS tier class for a fit score."""
    if score >= 80:
        return "js-fit-high"
    if score >= 60:
        return "js-fit-mid"
    return "js-fit-low"


def _fit_ring(score: int) -> str:
    """Render the fit score as a conic-gradient gauge."""
    return (
        f'<div class="js-fit-ring {_fit_class(score)}" style="--val:{score}" '
        f'role="img" aria-label="Fit score {score} out of 100">'
        f'<span class="js-fit-num">{score}</span></div>'
    )


def _job_card(ranked: RankedJob, index: int) -> str:
    """Render one ranked job as a card with a conic fit gauge and skill chips."""
    job = ranked.job
    title = escape(job.title)
    if job.url:
        title_html = f'<a class="js-job-title" href="{escape(job.url)}" target="_blank" rel="noopener">{title}</a>'
    else:
        title_html = f'<span class="js-job-title">{title}</span>'
    remote = '<span class="js-remote">Remote</span>' if job.remote else ""
    source = f'<span class="js-source">{escape(job.source)}</span>'
    matched = _chips(ranked.matched_skills, "match", 6)
    gaps = _chips(ranked.gaps, "gap", 4)
    delay = f"animation-delay:{min(index, 8) * 55}ms"
    return (
        f'<div class="js-job" style="{delay}">'
        '<div class="js-job-head">'
        f"<div>{title_html}"
        f'<div class="js-job-meta">{escape(job.company)} · {escape(job.location)}{remote}{source}</div></div>'
        f"{_fit_ring(ranked.fit_score)}"
        "</div>"
        f'<p class="js-job-why">{escape(ranked.fit_explanation)}</p>'
        f"{matched}{gaps}"
        "</div>"
    )


def _results_html(result: RunResult) -> str:
    """Render the ranked jobs as cards, or an empty state."""
    if not result.ranked_jobs:
        return (
            '<div class="js-empty"><div class="js-empty-icon">🔍</div>'
            "<div>No matching jobs found. Try a resume with more detail, or check back later.</div></div>"
        )
    cards = "".join(_job_card(r, i) for i, r in enumerate(result.ranked_jobs))
    return f'<div class="js-jobs">{cards}</div>'


def _footer_html(result: RunResult) -> str:
    """Render the run footer: cost, latency, job source, and the Opik link."""
    link = f'<a href="{result.opik_url or opik_url()}" target="_blank" rel="noopener">view traces in Opik ↗</a>'
    if result.failed:
        body = f"⚠ run failed — {escape(result.error_message)}. The trace has details · {link}"
    else:
        sources = escape(", ".join(result.jobs_sources) or "none")
        sep = ' <span class="js-muted">·</span> '
        body = (
            f'<span class="js-meta-mono">${result.cost_usd:.4f}</span>{sep}'
            f'<span class="js-meta-mono">{result.latency_s}s</span>{sep}'
            f"source: {sources}{sep}{link}"
        )
    return f'<div class="js-footer">{body}</div>'


def _cv_preview_html(pack) -> str:
    """Render the tailored CV with each bullet's corpus_ref chip (the grounding IS the feature)."""
    cv = pack.cv
    parts = [f"<p><b>{escape(cv.headline)}</b></p>", f'<p class="js-job-why">{escape(cv.summary)}</p>']
    for entry in cv.experience:
        dates = f' <span class="js-cvprev-dates">{escape(entry.dates)}</span>' if entry.dates else ""
        parts.append(f'<p class="js-cvprev-role">{escape(entry.role)} — {escape(entry.company)}{dates}</p>')
        if entry.bullets:
            bullets = "".join(
                f"<li>{escape(b.text)}<span class='js-ref'>{escape(b.corpus_ref)}</span></li>" for b in entry.bullets
            )
            parts.append(f"<ul>{bullets}</ul>")
    if cv.skills:
        pills = "".join(f'<span class="js-pill">{escape(s)}</span>' for s in cv.skills)
        parts.append(f'<div style="margin-top:10px">{pills}</div>')
    if cv.education:
        rows = "".join(f"<li>{escape(line)}</li>" for line in cv.education)
        parts.append(f"<ul>{rows}</ul>")
    return f'<div class="js-card js-cvprev">{"".join(parts)}</div>'


def _fabrication_html(result: TailorResult) -> str:
    """Render the validator verdict — never hidden, whatever it says."""
    if result.fabrication_flags == 0:
        return '<div class="js-fab-ok">✓ Every claim in this application traced back to your CV.</div>'
    report = result.fabrication_report
    items = ""
    if report:
        items = "".join(
            f"<li>“{escape(f.text[:140])}”<br><span class='js-fab-reason'>{escape(f.reason)}</span></li>" for f in report.flagged
        )
    n = result.fabrication_flags
    return (
        f'<div class="js-fab-warn"><b>⚠ {n} statement{"s" if n != 1 else ""} could not be verified against your CV.</b>'
        f" Review before sending.<ul>{items}</ul></div>"
    )


def _pack_html(result: TailorResult) -> str:
    """Render the application pack: cover letter, tailored CV, honesty note."""
    if result.pack is None:
        why = escape("; ".join(result.errors) or result.error_message or "unknown error")
        return f'<div class="js-empty"><div class="js-empty-icon">⚠</div><div>Could not tailor this job: {why}</div></div>'
    pack = result.pack
    honesty = ""
    if pack.honesty_note:
        honesty = f'<div class="js-honesty"><b>Honesty note</b> — {escape(pack.honesty_note)}</div>'
    return (
        '<div class="js-pack">'
        f"{_fabrication_html(result)}"
        '<p class="js-section-label">Cover letter</p>'
        f'<div class="js-card js-letter">{escape(pack.cover_letter)}</div>'
        '<p class="js-section-label">Tailored CV</p>'
        f"{_cv_preview_html(pack)}"
        f"{honesty}"
        "</div>"
    )


def _tailor_footer_html(result: TailorResult) -> str:
    """Run footer for the tailor step: cost, latency, research flag, Opik link."""
    link = f'<a href="{result.opik_url or opik_url()}" target="_blank" rel="noopener">view traces in Opik ↗</a>'
    if result.failed:
        body = f"⚠ run failed — {escape(result.error_message)}. The trace has details · {link}"
    else:
        sep = ' <span class="js-muted">·</span> '
        research = "with company research" if result.research_used else "no company research"
        body = (
            f'<span class="js-meta-mono">${result.cost_usd:.4f}</span>{sep}'
            f'<span class="js-meta-mono">{result.latency_s}s</span>{sep}'
            f"{research}{sep}{link}"
        )
    return f'<div class="js-footer">{body}</div>'


def _status(text: str, error: bool = False) -> str:
    """Render a status line on the start page."""
    cls = "js-status js-status-err" if error else "js-status"
    return f'<div class="{cls}">{escape(text)}</div>'


def _pack_downloads(result: TailorResult, profile: Profile | None) -> tuple[dict, dict, str]:
    """Footer + download-button updates for a finished tailoring run (click or voice)."""
    hidden = gr.update(visible=False)
    footer = _tailor_footer_html(result)
    pdf_btn, tex_btn = hidden, hidden
    if result.pack is not None:
        name = (profile.name if profile else None) or "Candidate"
        render = render_pdf(result.pack.cv, name, Path(tempfile.mkdtemp(prefix="job_scout_render_")))
        tex_btn = gr.update(value=str(render.tex_path), visible=True)
        if render.pdf_path is not None:
            pdf_btn = gr.update(value=str(render.pdf_path), visible=True)
        elif render.message:
            footer = f'<div class="js-footer">{escape(render.message)}</div>{footer}'
    return pdf_btn, tex_btn, footer


def _voice_status_html(status: str, message: str = "", hud: dict | None = None) -> str:
    """Render the Jobvis status line: level-driven orb + optional latency readout."""
    cls = {"active": "jv-active", "connecting": "jv-active", "error": "jv-error"}.get(status, "")
    labels = {"idle": "Jobvis is off", "connecting": "connecting…", "active": "Jobvis is listening", "error": "voice error"}
    label = message or labels.get(status, status)
    level = float((hud or {}).get("level") or 0.0)
    latency_ms = (hud or {}).get("latency_ms")
    latency = f'<span class="jv-lat">· {int(latency_ms)} ms</span>' if latency_ms and status == "active" else ""
    return (
        f'<div class="jv-status {cls}"><span class="jv-dot" style="--jv-level:{level:.3f}"></span>'
        f"{escape(label)}{latency}</div>"
    )


def _voice_context(text: str) -> None:
    """Whisper a screen event to a live Jobvis session (silent; no-op when idle)."""
    from job_scout.voice import get_voice_session  # lazy: the SDK loads only inside a running session

    get_voice_session().share_context(text)


def _run_announcement(run) -> str:
    """The 'System note' that makes Jobvis speak, unprompted, about a finished run."""
    if run.failed:
        return (
            f"System note: the {run.kind} failed ({run.error or 'unknown error'}). " "Apologize briefly and suggest trying again."
        )
    if run.kind == "search" and run.search_result is not None:
        ranked = run.search_result.ranked_jobs
        if not ranked:
            return (
                "System note: the job search finished but found no matching jobs. "
                "Break it gently; suggest a more detailed resume or trying again later."
            )
        top = ranked[0]
        return (
            f"System note: the job search just finished — {len(ranked)} jobs ranked, now on screen. "
            f"Top match: {top.job.title} at {top.job.company}, {top.fit_score} out of 100. "
            "Brief the user in one or two sentences and offer to run through the top three."
        )
    if run.kind == "tailor" and run.tailor_result is not None:
        result = run.tailor_result
        if result.pack is None:
            return "System note: tailoring finished but produced no application. Apologize and suggest trying again."
        flags = result.fabrication_flags
        verdict = (
            "every claim checked against the CV, no flags"
            if flags == 0
            else f"{flags} statement{'s' if flags != 1 else ''} could not be verified — advise an on-screen review"
        )
        return (
            "System note: the application pack is ready and on screen — cover letter and tailored CV with downloads. "
            f"Fabrication check: {verdict}. Announce it and offer the highlights."
        )
    return f"System note: the {run.kind} finished; the results are on screen."


def _transcript_html(lines: list[tuple[str, str]]) -> str:
    """Render the conversation transcript, tool calls included — the grounding on display."""
    if not lines:
        return '<div class="jv-transcript"><span class="jv-system">Nothing yet — start a session and say hello.</span></div>'
    rows = []
    for role, text in lines[-60:]:
        if role == "tool":
            rows.append(f'<div class="jv-tool">⚙ {escape(text)}</div>')
        elif role == "system":
            rows.append(f'<div class="jv-system">{escape(text)}</div>')
        else:
            rows.append(f'<div><b>{"You" if role == "you" else "Jobvis"}:</b> {escape(text)}</div>')
    return f'<div class="jv-transcript">{"".join(rows)}</div>'


def on_voice_toggle():
    """Start or stop the Jobvis voice session. Outputs: (voice_btn, voice_status)."""
    from job_scout.voice import get_voice_session  # lazy: needs the voice extra

    session = get_voice_session()
    status, _, _ = session.snapshot()
    if status in ("connecting", "active"):
        session.stop()
        return gr.update(value="Talk to Jobvis"), _voice_status_html("idle")
    ok, message = session.start()
    return (
        gr.update(value="Stop Jobvis" if ok else "Talk to Jobvis"),
        _voice_status_html("active" if ok else "error", message),
    )


def on_voice_tick():
    """Poll the voice session; keep the wizard in sync with a voice-triggered run.

    Most ticks are no-ops beyond the status/transcript refresh. While a run is
    IN FLIGHT the tick mirrors the click path's loading view — the right step
    page shows the runner's live status lines ("ranking 12 jobs…"), so the
    screen never sits frozen while Jobvis works. When the bridge hands over the
    finished run, the SAME renderers the click path uses fill the pages: a
    search pops step 3, a tailoring pops step 4 with the PDF — even if the user
    hung up the voice session during the wait.
    """
    from job_scout.voice import get_voice_session  # lazy: needs the voice extra

    session = get_voice_session()
    status, lines, error = session.snapshot()
    status_html = _voice_status_html(status, error if status == "error" else "", session.hud())
    transcript = _transcript_html(lines)
    no = gr.update()

    bridge = voice_bridge.get_bridge()
    run = bridge.pop_finished_run()
    if run is None:
        progress = bridge.run_status()
        if progress.get("running"):
            loading = _loading_html(str(progress.get("latest_status") or "working…"))
            if progress.get("kind") == "search":
                return (
                    status_html,
                    transcript,
                    gr.update(visible=False),
                    gr.update(visible=True),
                    gr.update(visible=False),
                    loading,
                    "",
                    no,
                    no,
                    no,
                    no,
                    no,
                )
            return (
                status_html,
                transcript,
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(visible=True),
                no,
                no,
                no,
                loading,
                "",
                gr.update(visible=False),
                gr.update(visible=False),
            )
    if run is not None:
        session.announce(_run_announcement(run))  # the butler brings the news himself
    if run is not None and run.kind == "search" and run.search_result is not None and not run.failed:
        result = run.search_result
        choices = [(f"{r.job.title} — {r.job.company} (fit {r.fit_score})", r.job.job_id) for r in result.ranked_jobs]
        return (
            status_html,
            transcript,
            gr.update(visible=False),
            gr.update(visible=True),
            gr.update(visible=False),
            _results_html(result),
            _footer_html(result),
            gr.update(choices=choices, value=None, visible=bool(choices)),
            no,
            no,
            no,
            no,
        )
    if run is not None and run.kind == "tailor" and run.tailor_result is not None and not run.failed:
        result = run.tailor_result
        pdf_btn, tex_btn, footer = _pack_downloads(result, voice_bridge.get_bridge().snapshot().profile)
        return (
            status_html,
            transcript,
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=True),
            no,
            no,
            no,
            _pack_html(result),
            footer,
            pdf_btn,
            tex_btn,
        )
    return (status_html, transcript, no, no, no, no, no, no, no, no, no, no)


def _ensure_jarvis_session() -> None:
    """Standalone /jarvis visit: claim a wizard thread and seed the saved candidate.

    The bridge is process-wide, so if the main page already registered a thread
    this is a no-op — the Jarvis page simply views the same session. (Two tabs,
    one bridge: last registered thread wins, as documented.)
    """
    bridge = voice_bridge.get_bridge()
    if bridge.snapshot().thread_id:
        return
    thread_id = str(uuid4())
    bridge.register_thread(thread_id)
    stored = candidate_store.load_candidate()
    if stored is not None:
        bridge.record_profile(_apply_preferences(stored.profile, stored.preferences), stored.cv_text, thread_id)


def _jarvis_orb_html(status: str, hud: dict | None, error: str = "") -> str:
    """The big reactive orb + status line for the Jarvis page."""
    level = float((hud or {}).get("level") or 0.0)
    latency_ms = (hud or {}).get("latency_ms")
    cls = {"active": "jx-live", "connecting": "jx-live", "error": "jx-err"}.get(status, "")
    labels = {"idle": "standing by", "connecting": "coming online…", "active": "listening", "error": "fault"}
    label = error or labels.get(status, status)
    latency = f'<span class="jv-lat">· {int(latency_ms)} ms</span>' if latency_ms and status == "active" else ""
    return (
        f'<div class="jx-orb-wrap {cls}" style="--jv-level:{level:.3f}"><div class="jx-orb"></div>'
        f'<div class="jx-orb-status">{escape(label)}{latency}</div></div>'
    )


def _jarvis_results_html(values: dict, snap, progress: dict) -> str:
    """The live top-matches panel: progress, results, or what to do next."""
    if progress.get("running"):
        body = f'<span class="jx-empty">{escape(str(progress.get("latest_status") or "working…"))}</span>'
        return f'<div class="jx-panel"><p class="jx-panel-title">Working</p>{body}</div>'
    ranked = list(values.get("ranked_jobs") or [])
    if ranked:
        rows = "".join(
            f'<div class="jx-row"><span class="jx-rank">{i:02d}</span>'
            f'<span class="jx-job"><b>{escape(r.job.title)}</b><br>'
            f'<span class="jx-meta">{escape(r.job.company)} · {escape(r.job.location)}</span></span>'
            f'<span class="jx-score">{r.fit_score}</span></div>'
            for i, r in enumerate(ranked[:3], 1)
        )
        return f'<div class="jx-panel"><p class="jx-panel-title">Top matches</p>{rows}</div>'
    if snap.profile is not None:
        return (
            '<div class="jx-panel"><p class="jx-panel-title">Ready</p>'
            f'<span class="jx-empty">Profile loaded for {escape(snap.profile.name or "the candidate")}. '
            "Say: “Find me jobs.”</span></div>"
        )
    return (
        '<div class="jx-panel"><p class="jx-panel-title">No candidate</p>'
        '<span class="jx-empty">Drop a CV in the <a href="http://localhost:7860" style="color:#66D9A8">main app</a> once — '
        "Jobvis remembers it from then on.</span></div>"
    )


_JARVIS_RENDER_CACHE: dict = {"key": None, "pdf": None, "tex": None}


def _jarvis_pack_panel(values: dict, snap) -> tuple[str, dict, dict]:
    """Application-ready panel + download buttons; the PDF renders once per pack."""
    hidden = gr.update(visible=False)
    pack = values.get("tailoring")
    if pack is None:
        return "", hidden, hidden
    if _JARVIS_RENDER_CACHE["key"] != (key := hash(pack.cover_letter)):
        name = (snap.profile.name if snap.profile else None) or "Candidate"
        render = render_pdf(pack.cv, name, Path(tempfile.mkdtemp(prefix="job_scout_render_")))
        _JARVIS_RENDER_CACHE.update(key=key, pdf=render.pdf_path, tex=render.tex_path)
    flags = int(values.get("fabrication_flags") or 0)
    verdict = (
        "✓ Every claim checked against the CV — no flags."
        if flags == 0
        else f"⚠ {flags} statement{'s' if flags != 1 else ''} could not be verified — review before sending."
    )
    html = (
        '<div class="jx-panel"><p class="jx-panel-title">Application ready</p>'
        f"<b>{escape(pack.cv.headline)}</b><br><span class='jx-meta'>{escape(verdict)}</span></div>"
    )
    pdf = gr.update(value=str(_JARVIS_RENDER_CACHE["pdf"]), visible=True) if _JARVIS_RENDER_CACHE["pdf"] else hidden
    tex = gr.update(value=str(_JARVIS_RENDER_CACHE["tex"]), visible=True) if _JARVIS_RENDER_CACHE["tex"] else hidden
    return html, pdf, tex


def on_jarvis_toggle():
    """Engage/stand down the voice session from the Jarvis page."""
    from job_scout.voice import get_voice_session  # lazy: needs the voice extra

    _ensure_jarvis_session()
    session = get_voice_session()
    status, _, _ = session.snapshot()
    if status in ("connecting", "active"):
        session.stop()
        return gr.update(value="Engage Jobvis")
    ok, _message = session.start()
    return gr.update(value="Stand down" if ok else "Engage Jobvis")


def on_jarvis_tick():
    """The Jarvis page's 1s heartbeat: orb, feed, matches, pack — all from shared state.

    Also pops/announces finished runs, so the page is self-sufficient when it is
    the only tab open; panels read the checkpoint directly, so whichever tab
    pops first, this view converges a tick later.
    """
    from job_scout.voice import get_voice_session  # lazy: needs the voice extra

    _ensure_jarvis_session()
    session = get_voice_session()
    status, lines, error = session.snapshot()
    bridge = voice_bridge.get_bridge()
    run = bridge.pop_finished_run()
    if run is not None:
        session.announce(_run_announcement(run))
    snap = bridge.snapshot()
    values = voice_bridge.checkpoint_values(snap.thread_id)
    pack_html_str, pdf_btn, tex_btn = _jarvis_pack_panel(values, snap)
    return (
        _jarvis_orb_html(status, session.hud(), error if status == "error" else ""),
        _transcript_html(lines),
        _jarvis_results_html(values, snap, bridge.run_status()),
        pack_html_str,
        pdf_btn,
        tex_btn,
        gr.update(value="Stand down" if status in ("connecting", "active") else "Engage Jobvis"),
    )


def _build_jarvis_page(voice_ok: bool, voice_hint: str) -> list | None:
    """The /jarvis page: a full-dark, fully voice-directed console.

    Returns the tick-output component list (None when voice is unavailable) so
    the caller can wire the priming load event.
    """
    gr.HTML('<div id="jx-backdrop"></div>')
    with gr.Column(elem_id="jx-root"):
        gr.HTML('<p class="jx-title">Jobvis</p><p class="jx-sub">voice-directed job scout</p>')
        if not voice_ok:
            gr.HTML(f'<p class="jv-hint">{escape(voice_hint)}</p>')
            return None
        jx_orb = gr.HTML(_jarvis_orb_html("idle", None))
        jx_btn = gr.Button("Engage Jobvis", variant="primary", size="lg")
        jx_results = gr.HTML("")
        jx_pack = gr.HTML("")
        with gr.Row():
            jx_pdf = gr.DownloadButton("Download tailored CV (PDF)", visible=False, variant="primary")
            jx_tex = gr.DownloadButton("Download .tex", visible=False, variant="secondary")
        with gr.Accordion("Transcript", open=False):
            jx_feed = gr.HTML(_transcript_html([]))

    # Wiring at Blocks ROOT level: a Timer created inside a layout container can
    # miss the client render tree, and its null instance kills the frontend at
    # dispatch_load_events (observed on 6.20).
    outputs = [jx_orb, jx_feed, jx_results, jx_pack, jx_pdf, jx_tex, jx_btn]
    jx_btn.click(on_jarvis_toggle, outputs=[jx_btn])
    gr.Timer(1.0).tick(on_jarvis_tick, outputs=outputs)
    return outputs


REMOTE_CHOICE = "Remote (anywhere)"


def _selection_to_prefs(selection: list[str]) -> dict:
    """The chooser's ticked boxes as a preferences dict."""
    return {"locations": [s for s in selection if s != REMOTE_CHOICE], "remote": REMOTE_CHOICE in selection}


def _apply_preferences(profile: Profile, preferences: dict | None) -> Profile:
    """The profile the SEARCH sees: extraction fields overridden by the human's choice.

    The stored profile stays untouched — it is what the extractor measured and
    what evaluation grades. Only the search runs on the chosen locations.
    """
    if not preferences:
        return profile
    return profile.model_copy(
        update={"locations": list(preferences.get("locations") or []), "remote_ok": bool(preferences.get("remote"))}
    )


def _preference_selection(profile: Profile, preferences: dict | None) -> tuple[list[str], list[str]]:
    """(choices, selected) for the chooser — stored preferences win, else the extraction's suggestion."""
    if preferences:
        locations = [loc for loc in (preferences.get("locations") or []) if loc]
        remote = bool(preferences.get("remote"))
    else:
        locations, remote = list(profile.locations), bool(profile.remote_ok)
    choices = [*dict.fromkeys([*profile.locations, *locations]), REMOTE_CHOICE]
    return choices, [*locations, *([REMOTE_CHOICE] if remote else [])]


def on_prefs_change(selection: list[str], profile: Profile | None, cv_text: str, thread_id: str) -> None:
    """Persist the chooser's state and keep the voice bridge on the same page."""
    if profile is None:
        return
    prefs = _selection_to_prefs(selection)
    voice_bridge.get_bridge().record_profile(_apply_preferences(profile, prefs), cv_text, thread_id)
    candidate_store.save_candidate(profile, cv_text, prefs)


def on_add_location(new_location: str, choices: list[str], selection: list[str], profile, cv_text, thread_id):
    """Add a typed location to the chooser, ticked, and persist.

    Outputs: (loc_choices_state, loc_group, loc_new_textbox).
    """
    location = (new_location or "").strip()
    if not location or location in choices:
        return choices, gr.update(), ""
    choices = [*choices[:-1], location, REMOTE_CHOICE] if choices else [location, REMOTE_CHOICE]
    selection = [*selection, location]
    on_prefs_change(selection, profile, cv_text, thread_id)  # programmatic updates don't fire .change
    return choices, gr.update(choices=choices, value=selection), ""


def _on_load(thread_id: str):
    """Register the wizard thread with the voice bridge and restore a saved candidate.

    With a stored candidate the app opens on step 2 — profile shown, chooser
    restored, Find jobs ready, Jobvis pre-seeded — and no LLM call happens
    (the extraction was paid for when the CV was first uploaded). Jobs are NOT
    restored: results go stale, so every session fetches fresh.
    Outputs: (page_start, page_profile, profile_html, cv_text_state,
    profile_state, loc_choices_state, loc_group).
    """
    voice_bridge.get_bridge().register_thread(thread_id)
    stored = candidate_store.load_candidate()
    if stored is None:
        return (gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update())
    choices, selected = _preference_selection(stored.profile, stored.preferences)
    effective = _apply_preferences(stored.profile, stored.preferences)
    voice_bridge.get_bridge().record_profile(effective, stored.cv_text, thread_id)
    note = (
        '<p class="js-muted" style="text-align:center;font-size:0.8rem;margin-top:10px">'
        "Restored from your last session — “Start over” forgets it.</p>"
    )
    return (
        gr.update(visible=False),
        gr.update(visible=True),
        _profile_html(stored.profile) + note,
        stored.cv_text,
        stored.profile,
        choices,
        gr.update(choices=choices, value=selected),
    )


def on_upload(file_path: str | None, thread_id: str):
    """Step 1 → 2: read the resume, extract the profile, and reveal the profile page.

    The chooser is seeded with the extraction's locations/remote as a ticked
    SUGGESTION — the human confirms or edits before any search runs.
    Outputs: (page_start, page_profile, profile_html, cv_text_state,
    start_status, profile_state, loc_choices_state, loc_group).
    """
    stay = gr.update(visible=True), gr.update(visible=False)
    go = gr.update(visible=False), gr.update(visible=True)
    no = gr.update()

    if not file_path:
        yield (*stay, gr.update(), "", _status("Please drop a PDF resume.", error=True), None, no, no)
        return
    try:
        cv_text = extract_cv_text(file_path)
    except CVReadError as exc:
        yield (*stay, gr.update(), "", _status(f"Could not read that PDF: {exc}", error=True), None, no, no)
        return

    yield (*go, _loading_html("Reading your resume…"), cv_text, "", gr.update(), no, no)
    try:
        profile = extract_profile(cv_text, thread_id=thread_id, tags=["phase-2", "ui", "extract"])
    except Exception as exc:  # noqa: BLE001 - show a friendly error and return to start
        msg = f"Couldn't read a profile: {exc}"
        if "api_key" in str(exc).lower() or "credentials" in str(exc).lower():
            msg += " Add an LLM key to .env (OPENAI_API_KEY, or a free SCOUT_MODEL via groq:/ollama:)."
        yield (*stay, gr.update(), "", _status(msg, error=True), None, no, no)
        return
    choices, selected = _preference_selection(profile, None)
    voice_bridge.get_bridge().record_profile(profile, cv_text, thread_id)
    candidate_store.save_candidate(profile, cv_text, _selection_to_prefs(selected))
    _voice_context(f"Screen event: the user just uploaded a CV; a profile was extracted for {profile.name or 'the candidate'}.")
    yield (*go, _profile_html(profile), cv_text, "", profile, choices, gr.update(choices=choices, value=selected))


def on_find(cv_text: str, profile: Profile | None, thread_id: str, loc_selection: list[str]):
    """Step 2 → 3: run the job-finding graph for the chosen locations and stream results.

    The search runs on the EFFECTIVE profile — extraction overridden by the
    chooser's ticked locations/remote — so the model builds its query around
    where the human actually wants to work, not where it guessed.
    Outputs: (page_profile, page_results, results_html, footer_html, job_select).
    """
    go = gr.update(visible=False), gr.update(visible=True)
    if profile is None:
        yield (gr.update(visible=True), gr.update(visible=False), gr.update(), "", gr.update())
        return

    prefs = _selection_to_prefs(loc_selection or [])
    effective = _apply_preferences(profile, prefs)
    voice_bridge.get_bridge().record_profile(effective, cv_text, thread_id)
    candidate_store.save_candidate(profile, cv_text, prefs)

    yield (*go, _loading_html("Searching for jobs…"), "", gr.update())
    result = RunResult()
    for kind, payload in stream_search(effective, cv_text=cv_text, thread_id=thread_id, tags=["phase-2", "ui"]):
        if kind == "status":
            yield (*go, _loading_html(str(payload)), "", gr.update())
        elif kind == "result":
            result = payload  # type: ignore[assignment]

    choices = [(f"{r.job.title} — {r.job.company} (fit {r.fit_score})", r.job.job_id) for r in result.ranked_jobs]
    select = gr.update(choices=choices, value=None, visible=bool(choices))
    voice_bridge.get_bridge().record_step("results")
    _voice_context(f"Screen event: an on-screen job search just finished — {len(result.ranked_jobs)} ranked jobs are visible.")
    yield (*go, _results_html(result), _footer_html(result), select)


def on_zip(zip_path: str | None) -> str | None:
    """Store the optional LinkedIn export path in session state (path only, never traced)."""
    voice_bridge.get_bridge().record_linkedin_zip(zip_path)
    return zip_path


def on_tailor(selected_job_id: str | None, thread_id: str, linkedin_zip: str | None, profile: Profile | None):
    """Step 3 → 4: tailor an application for the selected job on the SAME thread.

    Only ``selected_job_id`` (+ optional LinkedIn export path) is passed to the
    graph — profile, ranked jobs and CV text come from the thread's checkpoint.
    Outputs: (page_results, page_tailor, tailor_html, tailor_footer, pdf_btn, tex_btn).
    """
    stay = gr.update(visible=True), gr.update(visible=False)
    go = gr.update(visible=False), gr.update(visible=True)
    hidden = gr.update(visible=False)
    if not selected_job_id:
        gr.Warning("Pick a job from the dropdown first.")
        yield (*stay, gr.update(), gr.update(), hidden, hidden)
        return

    yield (*go, _loading_html("Drafting the application…"), "", hidden, hidden)
    result = TailorResult()
    stream = stream_tailor(
        thread_id=thread_id,
        selected_job_id=selected_job_id,
        tags=["phase-2", "ui", "tailor"],
        linkedin_zip_path=linkedin_zip,
    )
    for kind, payload in stream:
        if kind == "status":
            yield (*go, _loading_html(str(payload)), "", hidden, hidden)
        elif kind == "result":
            result = payload  # type: ignore[assignment]

    pdf_btn, tex_btn, footer = _pack_downloads(result, profile)
    voice_bridge.get_bridge().record_step("tailor")
    _voice_context("Screen event: an application pack was just tailored via the on-screen button and is now visible.")
    yield (*go, _pack_html(result), footer, pdf_btn, tex_btn)


def reset():
    """Return to step 1, clear the wizard, start a FRESH thread, forget the candidate.

    A new thread_id matters now that the checkpointer is shared: reusing the
    old thread for a different resume would mix two candidates' state. The
    persisted candidate is cleared too — "start over" is the explicit way to
    switch resumes, whereas an app restart keeps you.

    Outputs: (page_start, page_profile, page_results, page_tailor, cv_file,
    cv_text_state, start_status, results_html, footer_html, thread_id,
    linkedin_zip_state, job_select, tailor_out, tailor_footer, pdf_btn, tex_btn,
    loc_choices_state, loc_group).
    """
    new_thread_id = str(uuid4())
    voice_bridge.get_bridge().register_thread(new_thread_id)
    candidate_store.clear_candidate()
    return (
        gr.update(visible=True),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        None,
        "",
        "",
        "",
        "",
        new_thread_id,
        None,
        gr.update(choices=[], value=None),
        "",
        "",
        gr.update(visible=False),
        gr.update(visible=False),
        [],
        gr.update(choices=[], value=[]),
    )


def build_app() -> gr.Blocks:
    """Build the four-step wizard app."""
    register_prompts()

    with gr.Blocks(title="Job Scout") as demo:
        thread_id = gr.State(lambda: str(uuid4()))
        cv_text_state = gr.State("")
        profile_state = gr.State(None)
        linkedin_zip_state = gr.State(None)

        gr.HTML(
            f'<div id="js-header"><div class="js-mark">{_MARK}<h1>Job Scout</h1></div>'
            f'<div><span class="js-tag">{CAPTION}</span></div></div>'
        )

        voice_ok, voice_hint = is_voice_available()
        if voice_ok:
            with gr.Group(elem_id="jv-strip"):
                with gr.Row(elem_classes=["jv-row"]):
                    voice_btn = gr.Button("Talk to Jobvis", variant="secondary", size="sm", scale=0)
                    voice_status = gr.HTML(_voice_status_html("idle"))
                    gr.HTML('<a class="jv-link" href="http://localhost:7861" target="_blank" rel="noopener">Jarvis mode ↗</a>')
                with gr.Accordion("Transcript", open=False):
                    voice_transcript = gr.HTML(_transcript_html([]))
        else:
            gr.HTML(f'<p class="jv-hint">{escape(voice_hint)}</p>')

        with gr.Group(visible=True, elem_classes=["js-page"]) as page_start:
            gr.HTML(_stepper(1))
            gr.HTML(INTRO_HTML)
            cv_file = gr.File(label="", file_types=[".pdf"], type="filepath", height=150, elem_classes=["js-drop"])
            start_status = gr.HTML('<div class="js-status"></div>')

        with gr.Group(visible=False, elem_classes=["js-page"]) as page_profile:
            gr.HTML(_stepper(2))
            gr.HTML('<p class="js-section-label">Your profile</p>')
            profile_out = gr.HTML()
            gr.HTML('<p class="js-section-label" style="margin-top:14px">Where should we search?</p>')
            gr.HTML(
                '<p class="js-muted" style="font-size:0.86rem">The boxes come from your resume — a suggestion, '
                "not a decision. Tick where you actually want to work; add anywhere we missed.</p>"
            )
            loc_group = gr.CheckboxGroup(label="", show_label=False, choices=[], value=[], interactive=True)
            with gr.Row():
                loc_new = gr.Textbox(label="", show_label=False, placeholder="Add another location…", scale=4)
                loc_add = gr.Button("Add", size="sm", scale=0)
            find_btn = gr.Button("Find jobs", variant="primary", size="lg")
            with gr.Accordion("Add your LinkedIn export (optional)", open=False):
                gr.HTML(
                    '<p class="js-muted" style="font-size:0.86rem">Used only to enrich the tailoring step. '
                    "LinkedIn → Settings → “Get a copy of your data”. The ZIP stays on your machine and is "
                    "never attached to traces.</p>"
                )
                linkedin_file = gr.File(label="", file_types=[".zip"], type="filepath", height=90)
            change_btn = gr.Button("Upload a different resume", variant="secondary")

        with gr.Group(visible=False, elem_classes=["js-page"]) as page_results:
            gr.HTML(_stepper(3))
            gr.HTML('<p class="js-section-label">Ranked jobs</p>')
            results_out = gr.HTML()
            footer_out = gr.HTML()
            gr.HTML('<p class="js-section-label" style="margin-top:14px">Prepare an application</p>')
            job_select = gr.Dropdown(label="", choices=[], value=None, visible=False, interactive=True)
            tailor_btn = gr.Button("Tailor application", variant="primary")
            restart_btn = gr.Button("Start over", variant="secondary")

        with gr.Group(visible=False, elem_classes=["js-page"]) as page_tailor:
            gr.HTML(_stepper(4))
            gr.HTML('<p class="js-section-label">Application pack</p>')
            tailor_out = gr.HTML()
            with gr.Row():
                pdf_btn = gr.DownloadButton("Download tailored CV (PDF)", visible=False, variant="primary")
                tex_btn = gr.DownloadButton("Download .tex", visible=False, variant="secondary")
            tailor_footer = gr.HTML()
            back_btn = gr.Button("Back to jobs", variant="secondary")
            restart_btn2 = gr.Button("Start over", variant="secondary")

        loc_choices_state = gr.State([])
        cv_file.upload(
            on_upload,
            inputs=[cv_file, thread_id],
            outputs=[
                page_start,
                page_profile,
                profile_out,
                cv_text_state,
                start_status,
                profile_state,
                loc_choices_state,
                loc_group,
            ],
        )
        linkedin_file.change(on_zip, inputs=[linkedin_file], outputs=[linkedin_zip_state])
        loc_group.input(on_prefs_change, inputs=[loc_group, profile_state, cv_text_state, thread_id])
        loc_add.click(
            on_add_location,
            inputs=[loc_new, loc_choices_state, loc_group, profile_state, cv_text_state, thread_id],
            outputs=[loc_choices_state, loc_group, loc_new],
        )
        loc_new.submit(
            on_add_location,
            inputs=[loc_new, loc_choices_state, loc_group, profile_state, cv_text_state, thread_id],
            outputs=[loc_choices_state, loc_group, loc_new],
        )
        find_btn.click(
            on_find,
            inputs=[cv_text_state, profile_state, thread_id, loc_group],
            outputs=[page_profile, page_results, results_out, footer_out, job_select],
        )
        tailor_btn.click(
            on_tailor,
            inputs=[job_select, thread_id, linkedin_zip_state, profile_state],
            outputs=[page_results, page_tailor, tailor_out, tailor_footer, pdf_btn, tex_btn],
        )
        back_btn.click(
            lambda: (gr.update(visible=True), gr.update(visible=False)),
            outputs=[page_results, page_tailor],
        )
        reset_outputs = [
            page_start,
            page_profile,
            page_results,
            page_tailor,
            cv_file,
            cv_text_state,
            start_status,
            results_out,
            footer_out,
            thread_id,
            linkedin_zip_state,
            job_select,
            tailor_out,
            tailor_footer,
            pdf_btn,
            tex_btn,
            loc_choices_state,
            loc_group,
        ]
        change_btn.click(reset, outputs=reset_outputs)
        restart_btn.click(reset, outputs=reset_outputs)
        restart_btn2.click(reset, outputs=reset_outputs)

        if voice_ok:
            voice_btn.click(on_voice_toggle, outputs=[voice_btn, voice_status])
            gr.Timer(1.0).tick(
                on_voice_tick,
                outputs=[
                    voice_status,
                    voice_transcript,
                    page_profile,
                    page_results,
                    page_tailor,
                    results_out,
                    footer_out,
                    job_select,
                    tailor_out,
                    tailor_footer,
                    pdf_btn,
                    tex_btn,
                ],
            )
        # Restore on page load. A load event (not a Timer): on a MOUNTED app the
        # client only opens its event connection on the first event, so a timer
        # never fires unprimed — the /jarvis page hit the same bug. demo.load is
        # safe on a standalone mounted app; it was only demo.route multipage
        # that crashed on cross-page load events.
        demo.load(
            _on_load,
            inputs=[thread_id],
            outputs=[page_start, page_profile, profile_out, cv_text_state, profile_state, loc_choices_state, loc_group],
        )

    return demo


def build_jarvis_app() -> gr.Blocks:
    """The /jarvis console as its OWN Gradio app.

    Deliberately not a `demo.route` page: Gradio 6 multipage dispatches the
    index page's (auto-generated) load events on every page, which crashes the
    frontend when their outputs don't exist there. Two independent apps mounted
    on one FastAPI server share nothing in the browser — and share everything
    that matters (bridge, run manager, voice session, candidate store) in the
    process.
    """
    voice_ok, voice_hint = is_voice_available()
    with gr.Blocks(title="Jobvis", theme=THEME, css=CSS) as jarvis:
        outputs = _build_jarvis_page(voice_ok, voice_hint)
        if outputs is not None:
            # Priming load: on a mounted app the client only opens its queue
            # connection on the first event, so without this the 1s Timer never
            # starts and the page sits frozen until a click. It also fills the
            # panels immediately. (Safe here — the multipage load-event crash
            # applied to demo.route pages, not standalone apps.)
            jarvis.load(on_jarvis_tick, outputs=outputs)
    return jarvis


JARVIS_PORT = 7861


def main() -> None:
    """Serve the wizard on :7860 and the Jarvis console on :7861 — one process.

    Two launch()es rather than gr.mount_gradio_app, and the ORDER matters —
    both learned the hard way (Gradio 6.20): mounting two apps on one FastAPI
    server left the root app's queue/data stream answering 503, and with two
    launches the app launched LAST with the blocking call was the one whose
    events never reached the browser. Wizard first (prevent_thread_lock),
    Jarvis last: both verified healthy. The process-wide bridge shares all
    state between the two regardless of ports.
    """
    build_app().launch(server_name="127.0.0.1", server_port=7860, theme=THEME, css=CSS, prevent_thread_lock=True)
    build_jarvis_app().launch(server_name="127.0.0.1", server_port=JARVIS_PORT, theme=THEME, css=CSS)


if __name__ == "__main__":
    main()
