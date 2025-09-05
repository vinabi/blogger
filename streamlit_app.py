# Streamlit UI for LangGraph Blog Writing Agent
# -------------------------------------------------
# Notes:
# - Requires a GROQ API key. Put it in the sidebar.
# - Click "Generate Draft" to run the pipeline up to the Supervisor step.
# - Use "Revise draft" to iterate, or "Approve & Finalize" to produce final package.
# - Export final Markdown or a PDF (markdown → HTML → simple PDF builder).
#
# Inspired by your original notebook/script.


import os
import json
import streamlit as st
from typing import List, Dict
from langchain_groq import ChatGroq
from langchain.schema import SystemMessage, HumanMessage

# -------------------------------
# Minimal markdown → PDF builder
# -------------------------------
def _escape_pdf_text(s: str) -> str:
    return s.replace("\\\\", "\\\\\\\\").replace("(", "\\\\(").replace(")", "\\\\)")

def markdown_to_basic_pdf_bytes(md_text: str) -> bytes:
    """
    Very small PDF writer that renders plain text lines of the given Markdown.
    It ignores formatting; the goal is a dependable, no-external-deps PDF.
    """
    # Convert to plain text lines (strip markdown syntax)
    # For a lightweight approach we just remove '#' and backticks.
    plain = md_text.replace("\r\n", "\n")
    for ch in ["#", "*", "`"]:
        plain = plain.replace(ch, "")
    lines = [line.rstrip() for line in plain.split("\n")]

    # Build a single-page PDF with Helvetica 12pt, 8.5x11in (612x792 pt).
    width, height = 612, 792
    font_size = 12
    leading = 16
    start_x = 54
    start_y = height - 54

    # Compose content stream
    content_lines = [
        "BT",
        f"/F1 {font_size} Tf",
        f"{start_x} {start_y} Td",
        f"{leading} TL",
    ]
    for i, line in enumerate(lines):
        esc = _escape_pdf_text(line)
        if i == 0:
            content_lines.append(f"({_escape_pdf_text(esc)}) Tj")
        else:
            content_lines.append("T*")
            content_lines.append(f"({_escape_pdf_text(esc)}) Tj")
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("utf-8")
    stream_obj = f"<< /Length {len(content)} >>\nstream\n".encode("utf-8") + content + b"\nendstream\n"

    # PDF objects
    objects = []
    # 1: Catalog
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    # 2: Pages
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    # 3: Page
    page_dict = f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
    objects.append(page_dict.encode("utf-8"))
    # 4: Content stream
    objects.append(stream_obj)
    # 5: Font
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Name /F1 >>")

    # Assemble xref table
    offsets = []
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode("utf-8") + obj + b"endobj\n"
    xref_start = len(pdf)
    pdf += f"xref\n0 {len(objects)+1}\n".encode("utf-8")
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n".encode("utf-8")
    # trailer
    pdf += b"trailer\n"
    pdf += f"<< /Size {len(objects)+1} /Root 1 0 R >>\n".encode("utf-8")
    pdf += b"startxref\n"
    pdf += f"{xref_start}\n".encode("utf-8")
    pdf += b"%%EOF"
    return bytes(pdf)


# -------------------------------
# LLM helpers
# -------------------------------
def make_llm(api_key: str, model: str = "llama-3.1-8b-instant", temperature: float = 0.4):
    if not api_key:
        raise ValueError("Please provide a GROQ API key in the sidebar.")
    return ChatGroq(model=model, api_key=api_key, temperature=temperature)

def ideas_researcher(llm, topic: str, audience: str, tone: str) -> Dict:
    sys = SystemMessage(content="You are an idea researcher. Provide concise idea bullets and 6-10 SEO keywords.")
    usr = HumanMessage(content=f"Topic: {topic}\nAudience: {audience}\nTone: {tone}")
    ideas = llm.invoke([sys, usr]).content
    return {"ideas": ideas}

def outliner(llm, ideas: str, keywords: List[str] | None = None) -> str:
    kw_str = ", ".join(keywords or [])
    sys = SystemMessage(content="You are an expert outliner. Use markdown H2/H3; include intro & conclusion.")
    usr = HumanMessage(content=f"Ideas:\n{ideas}\nKeywords: {kw_str}")
    outline = llm.invoke([sys, usr]).content
    return outline

def writer(llm, topic: str, audience: str, tone: str, target_words: int, outline: str, instructions: str = "") -> str:
    sys = SystemMessage(content="You are a blog writer. Produce a cohesive draft with headings, TL;DR, and clear paragraphs.")
    usr = HumanMessage(content=(
        f"Topic: {topic}\nAudience: {audience}\nTone: {tone}\n"
        f"Target words: {target_words}\nOutline:\n{outline}\n"
        f"Extra instructions: {instructions}"
    ))
    return llm.invoke([sys, usr]).content

def supervisor(llm, draft: str) -> str:
    sys = SystemMessage(content="You are a strict supervisor. Give 3–6 concrete improvement notes if needed; else say APPROVED.")
    usr = HumanMessage(content=f"Evaluate this draft for quality, tone, structure, coherence:\n\n{draft}")
    return llm.invoke([sys, usr]).content

def finalizer(llm, draft: str, notes: str, tone: str) -> Dict:
    sys = SystemMessage(content=(
        "You are a content ops specialist. Return strict JSON with keys: "
        "title (<=60 chars), meta (<=160 chars), slug, tags (list of 3-5), body_md (final markdown). "
        "Keep the user's tone."
    ))
    usr = HumanMessage(content=f"Draft:\n{draft}\nNotes:\n{notes}\nTone: {tone}")
    result = llm.invoke([sys, usr]).content
    try:
        data = json.loads(result)
        # sanity
        if not all(k in data for k in ("title", "meta", "slug", "tags", "body_md")):
            raise ValueError("Missing keys in JSON.")
        return data
    except Exception:
        # Fallback: wrap as best-effort
        return {
            "title": "Untitled",
            "meta": "",
            "slug": "untitled",
            "tags": [],
            "body_md": result,
        }


# -------------------------------
# Streamlit UI
# -------------------------------
st.set_page_config(page_title="Blogger", page_icon="🧩", layout="wide")

with st.sidebar:
    st.header("Settings")
    groq_key = st.text_input("GROQ API Key", type="password", help="Get one from https://console.groq.com/")
    model = st.text_input("Model", value="llama-3.1-8b-instant")
    temperature = st.slider("Temperature", 0.0, 1.0, 0.4, 0.05)

    st.markdown("---")
    st.subheader("Markdown → PDF")
    md_upload = st.file_uploader("Upload .md to convert", type=["md", "markdown"])
    if st.button("Convert uploaded Markdown to PDF"):
        if md_upload is not None:
            md_text = md_upload.read().decode("utf-8", errors="ignore")
            pdf_bytes = markdown_to_basic_pdf_bytes(md_text)
            st.download_button("Download PDF", pdf_bytes, file_name="converted.pdf", mime="application/pdf")
        else:
            st.warning("Please upload a markdown file first.")

st.title("LangGraph Blog Writing Agent — Blogger")

with st.form("profile_form"):
    topic = st.text_input("Topic", placeholder="e.g., Retrieval-Augmented Generation (RAG) in production")
    audience = st.text_input("Audience", placeholder="e.g., ML engineers, content strategists")
    tone = st.selectbox("Tone", ["friendly", "casual", "professional", "technical"], index=0)
    target_words = st.number_input("Target word count", min_value=300, max_value=4000, value=900, step=100)
    instructions = st.text_area("Any extra instructions?", placeholder="Constraints, citations, style preferences...")

    submitted = st.form_submit_button("Generate Draft")

if "state" not in st.session_state:
    st.session_state.state = {}

if submitted:
    try:
        llm = make_llm(groq_key, model=model, temperature=temperature)
    except Exception as e:
        st.error(str(e))
        st.stop()

    with st.spinner("Generating ideas & outline..."):
        idea_pack = ideas_researcher(llm, topic, audience, tone)
        outline = outliner(llm, idea_pack["ideas"])

    with st.spinner("Writing draft..."):
        draft = writer(llm, topic, audience, tone, int(target_words), outline, instructions)

    with st.spinner("Supervisor review..."):
        notes = supervisor(llm, draft)

    st.session_state.state = {
        "topic": topic,
        "audience": audience,
        "tone": tone,
        "target_words": int(target_words),
        "instructions": instructions,
        "ideas": idea_pack["ideas"],
        "outline": outline,
        "draft": draft,
        "supervisor_notes": notes,
        "final": None,
        "revisions": 0,
    }

if st.session_state.state.get("draft"):
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Ideas / Outline")
        with st.expander("Ideas (LLM)", expanded=False):
            st.markdown(st.session_state.state["ideas"])
        st.markdown("#### Outline")
        st.markdown(st.session_state.state["outline"])

    with col2:
        st.subheader("Draft")
        st.markdown(st.session_state.state["draft"])

    st.subheader("Supervisor notes")
    st.markdown(st.session_state.state["supervisor_notes"])

    c1, c2, c3 = st.columns([1,1,2])
    with c1:
        if st.button("Revise draft"):
            try:
                llm = make_llm(groq_key, model=model, temperature=temperature)
            except Exception as e:
                st.error(str(e))
                st.stop()
            with st.spinner("Revising..."):
                extra = st.session_state.state["instructions"] + "\n\nRevise based on these notes:\n" + st.session_state.state["supervisor_notes"]
                new_draft = writer(llm,
                                   st.session_state.state["topic"],
                                   st.session_state.state["audience"],
                                   st.session_state.state["tone"],
                                   int(st.session_state.state["target_words"]),
                                   st.session_state.state["outline"],
                                   extra)
                st.session_state.state["draft"] = new_draft
                st.session_state.state["revisions"] += 1
                st.experimental_rerun()

    with c2:
        if st.button("Approve & Finalize"):
            try:
                llm = make_llm(groq_key, model=model, temperature=temperature)
            except Exception as e:
                st.error(str(e))
                st.stop()
            with st.spinner("Finalizing..."):
                pack = finalizer(llm,
                                 st.session_state.state["draft"],
                                 st.session_state.state["supervisor_notes"],
                                 st.session_state.state["tone"])
                st.session_state.state["final"] = pack
            st.experimental_rerun()

if st.session_state.state.get("final"):
    final = st.session_state.state["final"]
    st.success("Final package ready!")
    st.markdown(f"### Title\n{final.get('title','')}")
    st.markdown(f"**Meta:** {final.get('meta','')}  \n**Slug:** `{final.get('slug','')}`  \n**Tags:** {', '.join(final.get('tags', []))}")
    st.markdown("### Final Markdown")
    st.markdown(final.get("body_md",""))

    md_bytes = final.get("body_md","").encode("utf-8")
    st.download_button("Download Markdown", md_bytes, file_name="final.md", mime="text/markdown")

    pdf_bytes = markdown_to_basic_pdf_bytes(final.get("body_md",""))
    st.download_button("Download PDF", pdf_bytes, file_name="final.pdf", mime="application/pdf")
