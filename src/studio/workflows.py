"""The prompts of the studio's chat, and its eight guided workflows.

Carried over from the standalone app the studio replaces. Each workflow has
what the page shows (title, description, form fields) and two prompt pieces:
``system``, added to the project's system prompt, and ``template``, filled in
with the form's values as the chat's opening message. Attached materials go
with that message. The prompt texts are the standalone app's, word for word,
except that the base prompt no longer names that app (and the pitch deck's
description names the studio's own buttons; see there).

``build_system`` puts the system prompt together: the base text with the
project's name, logline and notes; the workflow's text; the project's saved
documents within a 16,000-character budget, the most recently changed first
(src/studio/documents.py); and the voice references within a 12,000-character
budget, each only when the person sending may open it (src/studio/chats.py
collects them).
"""

BASE_SYSTEM = """You are a marketing and pitch assistant for independent filmmakers.
You help create marketing material for investors and for advertising on websites and social media.

Ground everything in the project details and any attached materials (slide decks, posters, stills).
If the attached materials contradict the brief, point it out. If key information is missing, make
your best assumption, state it, and continue — don't stall on questions.

Write in clean Markdown. Use headings and tables where they help. Content should be usable
as-is: no meta commentary about being an AI, no placeholder lorem ipsum.

Project: {name}
Logline / description: {logline}
Notes: {notes}"""

VOICE_SYSTEM = """VOICE & TONE REFERENCE
The user has provided transcript excerpts of material whose voice they want you to match.
Study the rhythm, sentence length, vocabulary, humour, and energy of these excerpts and write
everything in that voice. Match the style only — never copy or reference their content.

{examples}"""

# Total transcript characters injected into the system prompt. Keeps very long
# transcripts from crowding out the actual task; excerpts are enough for tone.
MAX_VOICE_PROMPT_CHARS = 12_000

KNOWLEDGE_SYSTEM = """PROJECT KNOWLEDGE
Documents previously saved in this project. Treat them as established context: keep new
material consistent with decisions made in them (taglines, synopses, budgets, positioning),
and when the user refers to "the tagline" or "our synopsis", this is what they mean. If the
user's new request contradicts them, follow the request — these are background, not orders.

{documents}"""

# Same idea as MAX_VOICE_PROMPT_CHARS: newest documents get an excerpt within
# this shared budget; anything past it is listed by title only.
MAX_KNOWLEDGE_PROMPT_CHARS = 16_000

MAX_FIELD_LENGTH = 2_000


def knowledge_section(documents):
    """documents: [{"title", "kind", "body"}], newest first. "" when there are none."""
    if not documents:
        return ""
    chunks, spent = [], 0
    for row in documents:
        if spent >= MAX_KNOWLEDGE_PROMPT_CHARS:
            chunks.append(f'--- "{row["title"]}" ({row["kind"]}) — older document, content omitted ---')
            continue
        excerpt = row["body"][: MAX_KNOWLEDGE_PROMPT_CHARS - spent]
        spent += len(excerpt)
        truncated = " …[truncated]" if len(excerpt) < len(row["body"]) else ""
        chunks.append(f'--- "{row["title"]}" ({row["kind"]}) ---\n{excerpt}{truncated}')
    return "\n\n" + KNOWLEDGE_SYSTEM.format(documents="\n\n".join(chunks))


def voice_section(examples):
    """examples: [{"name", "content"}], oldest first. "" when there are none."""
    examples = [e for e in examples if e.get("content")]
    if not examples:
        return ""
    budget = MAX_VOICE_PROMPT_CHARS // len(examples)
    chunks = []
    for row in examples:
        excerpt = row["content"][:budget]
        chunks.append(f'--- Excerpt from "{row["name"]}" ---\n{excerpt}')
    return "\n\n" + VOICE_SYSTEM.format(examples="\n\n".join(chunks))


def build_system(project, kind, documents=(), voice=()):
    """The system prompt for a chat of this kind in this project."""
    system = BASE_SYSTEM.format(
        name=project.name,
        logline=project.logline or "(not provided)",
        notes=project.notes or "(none)",
    )
    workflow = WORKFLOWS.get(kind)
    if workflow:
        system += "\n\n" + workflow["system"]
    system += knowledge_section(list(documents))
    system += voice_section(list(voice))
    return system


def render_opening_message(kind, fields):
    """The opening message of a workflow chat, from its form's values."""
    workflow = WORKFLOWS[kind]
    fields = fields if isinstance(fields, dict) else {}
    values = {}
    for f in workflow["fields"]:
        value = fields.get(f["id"])
        value = value.strip()[:MAX_FIELD_LENGTH] if isinstance(value, str) else ""
        values[f["id"]] = value or "(no preference — use your judgement)"
    return workflow["template"].format(**values)


def public_list():
    """The workflows as the page shows them: no prompt text."""
    return [{
        "id": key,
        "title": w["title"],
        "description": w["description"],
        "web_search": w["web_search"],
        "fields": w["fields"],
    } for key, w in WORKFLOWS.items()]


WORKFLOWS = {
    "marketing": {
        "title": "Marketing scripts & ad copy",
        "description": "Promo/trailer video scripts, website copy, and social ad variants.",
        "web_search": False,
        "fields": [
            {"id": "audience", "label": "Target audience", "placeholder": "e.g. festival programmers, horror fans 18-35, family audiences"},
            {"id": "tone", "label": "Tone", "placeholder": "e.g. suspenseful, heartfelt, playful"},
            {"id": "formats", "label": "What do you need?", "placeholder": "e.g. 60s trailer VO script, landing page copy, 3 Instagram ads"},
        ],
        "system": (
            "Focus: marketing scripts and advertising copy. Video scripts should use a clear "
            "scene/shot + voice-over structure with rough timings. Ad copy should include strong "
            "hooks and calls to action, with character-count-appropriate variants per platform."
        ),
        "template": (
            "Create marketing material for this project, based on the attached materials.\n\n"
            "Target audience: {audience}\n"
            "Tone: {tone}\n"
            "Deliverables: {formats}\n\n"
            "Start with a one-paragraph creative rationale, then produce each deliverable under "
            "its own heading."
        ),
    },
    "campaign": {
        "title": "Campaign bundle",
        "description": "One brief → a coordinated set: taglines, landing copy, email sequence, and social posts per platform.",
        "web_search": False,
        "fields": [
            {"id": "goal", "label": "Campaign goal", "placeholder": "e.g. sell premiere tickets, grow the newsletter before crowdfunding launches"},
            {"id": "audience", "label": "Target audience", "placeholder": "e.g. indie horror fans 18-35, local press, festival-goers"},
            {"id": "channels", "label": "Channels", "placeholder": "e.g. Instagram, TikTok, newsletter, website"},
            {"id": "cta", "label": "Call to action & key dates", "placeholder": "e.g. buy tickets for the Oct 12 premiere — link in bio"},
        ],
        "system": (
            "Focus: one coherent campaign generated from a single brief. Everything must flow from "
            "one creative platform (the campaign's big idea) so every asset unmistakably belongs to "
            "the same campaign — shared hooks, imagery, and phrases. Respect each channel's native "
            "format and length conventions. Every asset ends in the campaign's call to action."
        ),
        "template": (
            "Build a complete, coordinated marketing campaign for this project from the attached "
            "materials.\n\n"
            "Campaign goal: {goal}\n"
            "Target audience: {audience}\n"
            "Channels: {channels}\n"
            "Call to action & key dates: {cta}\n\n"
            "Produce, in this order:\n"
            "1) The creative platform — the campaign's big idea in 2-3 sentences, plus the shared "
            "hooks/phrases every asset will reuse\n"
            "2) 5 tagline options\n"
            "3) Landing page copy (headline, subhead, 3 short sections, CTA button text)\n"
            "4) A 3-email sequence (subject line + body each: tease, story, last call)\n"
            "5) 3 social posts per channel listed above, in each platform's native style\n"
            "6) 2 short paid-ad variants (primary text + headline)"
        ),
    },
    "investor": {
        "title": "Investor materials",
        "description": "Pitch summary, one-pager, talking points, and tough Q&A prep.",
        "web_search": False,
        "fields": [
            {"id": "ask", "label": "What are you asking investors for?", "placeholder": "e.g. £250k production budget, distribution partners"},
            {"id": "stage", "label": "Project stage", "placeholder": "e.g. development, pre-production, rough cut done"},
            {"id": "extras", "label": "Anything to emphasise?", "placeholder": "e.g. attached cast, prior festival wins, comparable successes"},
        ],
        "system": (
            "Focus: investor-facing materials. Be persuasive but honest — investors distrust hype. "
            "Where the attached deck provides numbers, use them exactly; never invent figures. "
            "Always include a 'Tough questions investors will ask' section with suggested answers."
        ),
        "template": (
            "Prepare investor materials for this project, grounded in the attached deck/materials.\n\n"
            "The ask: {ask}\n"
            "Project stage: {stage}\n"
            "Emphasise: {extras}\n\n"
            "Produce: 1) a pitch summary (2-3 paragraphs), 2) a one-pager outline, 3) key talking "
            "points, 4) tough questions investors will ask, each with a suggested answer."
        ),
    },
    "planning": {
        "title": "Planning & ideation",
        "description": "Campaign plans, brainstorms, and question checklists.",
        "web_search": False,
        "fields": [
            {"id": "goal", "label": "What are you planning?", "placeholder": "e.g. festival launch campaign, crowdfunding push, website relaunch"},
            {"id": "timeline", "label": "Timeline & budget", "placeholder": "e.g. 8 weeks, ~£2k ad spend"},
            {"id": "channels", "label": "Channels you can use", "placeholder": "e.g. Instagram, newsletter, film Twitter, local press"},
        ],
        "system": (
            "Focus: planning and idea creation. Give concrete, prioritised, actionable plans with "
            "phases and owners, plus a 'questions you should be asking yourself' checklist that "
            "surfaces blind spots. Offer several distinct creative angles, not variations of one idea."
        ),
        "template": (
            "Help me plan, based on the project and attached materials.\n\n"
            "Planning goal: {goal}\n"
            "Timeline & budget: {timeline}\n"
            "Available channels: {channels}\n\n"
            "Produce: 1) a brainstorm of distinct angles/hooks, 2) a phased plan with concrete "
            "actions, 3) a checklist of questions I should be asking before committing."
        ),
    },
    "coverage": {
        "title": "Script coverage",
        "description": "Attach your screenplay (PDF) and get professional-style coverage: synopsis, notes, audience personas, comps, and a verdict.",
        "web_search": False,
        "fields": [
            {"id": "format", "label": "Format & length", "placeholder": "e.g. feature ~95 pages, 20-min short, 6x30 series"},
            {"id": "focus", "label": "What do you most want feedback on?", "placeholder": "e.g. structure and pacing, dialogue, marketability"},
            {"id": "market", "label": "Intended market / audience", "placeholder": "e.g. festival circuit, streaming sale, UK theatrical"},
        ],
        "system": (
            "Focus: professional script coverage, as written by an experienced story analyst for a "
            "production company. Read the attached screenplay fully before judging. Be honest and "
            "specific — vague praise helps nobody. Point to actual scenes, characters, and lines "
            "when making a claim (with page numbers where visible). Being constructive means every "
            "weakness comes with a concrete suggestion. If no screenplay is attached, say so and "
            "stop rather than inventing coverage."
        ),
        "template": (
            "Give me full coverage on the attached screenplay.\n\n"
            "Format & length: {format}\n"
            "Feedback focus: {focus}\n"
            "Intended market / audience: {market}\n\n"
            "Produce, in this order:\n"
            "1) Logline (one sentence, as you'd pitch it)\n"
            "2) Synopsis (one page)\n"
            "3) Coverage notes on concept, structure & pacing, characters, and dialogue — "
            "each with specific scene references\n"
            "4) Strengths (what to protect in rewrites)\n"
            "5) Weaknesses, each with a concrete fix\n"
            "6) 2-3 audience personas who this is for\n"
            "7) Comparable titles and what their trajectory suggests for this project\n"
            "8) Verdict: PASS / CONSIDER / RECOMMEND, with rationale"
        ),
    },
    "pitchdeck": {
        "title": "Pitch deck",
        # The standalone app's description went on: "Save the result with 💾, then
        # download it as a real .pptx from Documents." Here the button has a name.
        "description": "A slide-by-slide investor pitch deck. Save the result with “Save as document”, "
                       "then download it as PowerPoint slides from the Documents tab.",
        "web_search": False,
        "fields": [
            {"id": "purpose", "label": "Who is the deck for?", "placeholder": "e.g. investors for a £250k raise, distribution partners, festival funds"},
            {"id": "slides", "label": "How many slides?", "placeholder": "e.g. 10 (default if blank)"},
            {"id": "emphasis", "label": "What must come across?", "placeholder": "e.g. team track record, market comps, attached cast"},
        ],
        "system": (
            "Focus: an investor pitch deck, written as slides — not prose. Format is strict, because "
            "the output is converted to PowerPoint: exactly one '## Slide title' heading per slide, "
            "each followed by 3-6 short bullets ('- '). No tables, no paragraphs, no text outside "
            "the slide structure. The first slide is the title slide: the project name as its "
            "heading, with a tagline and contact placeholder as bullets. Unless told otherwise, "
            "cover: hook/logline, story, why now, audience & market, comparables, team, budget & "
            "the ask, use of funds, timeline, contact. Use numbers from the attached materials "
            "exactly; never invent figures — write [FILL IN: …] instead."
        ),
        "template": (
            "Write a pitch deck for this project from the attached materials.\n\n"
            "Deck audience / purpose: {purpose}\n"
            "Slide count: {slides}\n"
            "Must come across: {emphasis}\n\n"
            "One '## Slide title' heading per slide, 3-6 punchy bullets each. Slide copy, "
            "not prose — every bullet short enough to sit on a slide."
        ),
    },
    "epk": {
        "title": "EPK generator",
        "description": "Electronic press kit: fact sheet, synopses at every length, bios, press release, and stills captions.",
        "web_search": False,
        "fields": [
            {"id": "hook", "label": "News hook / occasion", "placeholder": "e.g. world premiere at FrightFest Oct 12, trailer launch, distribution deal signed"},
            {"id": "people", "label": "Key people (names + past credits)", "placeholder": "e.g. director Jane Doe (BIFA-nominated short 'Ash'), lead Sam Roe ('Casualty')"},
            {"id": "facts", "label": "Key facts", "placeholder": "e.g. 92 min, shot on location in Wales, festival selections, release plans"},
        ],
        "system": (
            "Focus: an electronic press kit journalists can lift from directly. Everything must be "
            "copy-paste ready in a neutral press voice — no marketing hype, no superlatives a "
            "journalist would have to cut. Synopses must respect their stated lengths. Never invent "
            "credits, quotes, dates, or biography details: where a fact is missing, insert a clearly "
            "marked [FILL IN: …] gap instead of guessing. The press release follows standard "
            "structure: headline, dateline, lede with the news hook, body, quote placeholders, "
            "boilerplate, and a press contact placeholder."
        ),
        "template": (
            "Build a complete electronic press kit for this project from the attached materials.\n\n"
            "News hook / occasion: {hook}\n"
            "Key people: {people}\n"
            "Key facts: {facts}\n\n"
            "Produce, in this order:\n"
            "1) Fact sheet — title, genre, runtime, country, language, and a key-credits table\n"
            "2) Synopses — logline (max 25 words), short synopsis (~100 words), full synopsis "
            "(~1 page, no spoilers past the midpoint)\n"
            "3) Bios — a short (~75 word) bio per key person listed above\n"
            "4) Press release built on the news hook\n"
            "5) Stills captions — if images are attached, one caption per image in press style "
            "(who/what/where + credit line); otherwise 5 caption templates to fill in\n"
            "6) Suggested pull quotes — 3 lines from the materials a journalist could quote"
        ),
    },
    "comparison": {
        "title": "Competitor comparison",
        "description": "Web-researched comparison with similar films/products and how you stand out.",
        "web_search": True,
        "fields": [
            {"id": "competitors", "label": "Known comparables (optional)", "placeholder": "e.g. similar films, or leave blank to let the AI find them"},
            {"id": "market", "label": "Market / territory", "placeholder": "e.g. UK indie horror, streaming, festival circuit"},
            {"id": "angle", "label": "What comparison matters most?", "placeholder": "e.g. audience overlap, budget vs box office, marketing approach"},
        ],
        "system": (
            "Focus: competitive positioning. Use web search to find current, real information about "
            "comparable projects — do not rely on memory for anything recent. Cite where key facts "
            "came from. Build a comparison table, then a clear 'how we stand out' positioning "
            "section tailored to this project's actual strengths from the attached materials."
        ),
        "template": (
            "Research how this project compares to similar ones, using web search for current facts.\n\n"
            "Known comparables: {competitors}\n"
            "Market / territory: {market}\n"
            "Comparison focus: {angle}\n\n"
            "Produce: 1) a shortlist of the most relevant comparables with a sentence on why each "
            "is relevant, 2) a comparison table, 3) 'how we stand out' positioning, 4) what we can "
            "steal/learn from their marketing."
        ),
    },
}
