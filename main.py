import os, json, re
from datetime import datetime
from fastapi import FastAPI, Depends, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from pydantic import BaseModel
from typing import Optional
import anthropic

from models import Base, TestSession, QuestionResult

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./mcat.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="MCAT Coach")
app.mount("/public", StaticFiles(directory="public"), name="public")

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

PARSE_PROMPT = """You are analyzing a screenshot of an MCAT practice test results page.

Extract all available information and return ONLY valid JSON with this exact structure:
{
  "source": "UWorld" | "Khan Academy" | "Princeton Review" | "Kaplan" | "AAMC" | "Blueprint" | "Examkrackers" | "Other",
  "section": "Bio/Biochem" | "Chem/Phys" | "CARS" | "Psych/Soc" | "Full Length" | "Mixed" | "Unknown",
  "date": "YYYY-MM-DD or null",
  "total_questions": number or null,
  "correct": number or null,
  "questions": [
    {
      "number": number or null,
      "topic": "specific topic as shown, e.g. 'Enzyme Kinetics', 'Amino Acids', 'Electrochemistry'",
      "user_answer": "A" | "B" | "C" | "D" | null,
      "correct_answer": "A" | "B" | "C" | "D" | null,
      "is_correct": true | false,
      "time_spent_seconds": number or null
    }
  ]
}

Rules:
- Extract every question visible in the screenshot
- If topic is not shown, use the content area visible (e.g. "Biology", "General Chemistry")
- If a field is not visible in the screenshot, use null
- Return ONLY the JSON, no explanation or markdown"""


def parse_screenshot(image_base64: str, media_type: str = "image/jpeg"):
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": image_base64}
                },
                {"type": "text", "text": PARSE_PROMPT}
            ]
        }]
    )
    text = response.content[0].text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return json.loads(text)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_token(x_user_token: str = Header(default="")) -> str:
    return x_user_token


@app.get("/")
def root():
    return FileResponse("public/index.html")


class UploadRequest(BaseModel):
    image_base64: str
    media_type: str = "image/jpeg"
    notes: str = ""


@app.post("/sessions")
def create_session(req: UploadRequest, db: Session = Depends(get_db), token: str = Depends(get_token)):
    try:
        data = parse_screenshot(req.image_base64, req.media_type)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not parse screenshot: {str(e)}")

    questions_data = data.get("questions", [])
    total = data.get("total_questions") or len(questions_data)
    correct_count = data.get("correct")
    if correct_count is None:
        correct_count = sum(1 for q in questions_data if q.get("is_correct"))

    session = TestSession(
        token=token,
        date=data.get("date") or datetime.utcnow().strftime("%Y-%m-%d"),
        source=data.get("source", "Unknown"),
        section=data.get("section", "Unknown"),
        total_questions=total,
        correct=correct_count,
        notes=req.notes,
    )
    db.add(session)
    db.flush()

    for q in questions_data:
        db.add(QuestionResult(
            token=token,
            session_id=session.id,
            question_number=q.get("number"),
            topic=q.get("topic") or "Unknown",
            user_answer=q.get("user_answer"),
            correct_answer=q.get("correct_answer"),
            is_correct=bool(q.get("is_correct")),
            time_spent=q.get("time_spent_seconds"),
        ))

    db.commit()
    db.refresh(session)
    return {
        "id": session.id,
        "date": session.date,
        "source": session.source,
        "section": session.section,
        "total_questions": session.total_questions,
        "correct": session.correct,
        "accuracy": round(session.correct / session.total_questions * 100) if session.total_questions else 0,
        "questions_extracted": len(questions_data),
    }


@app.get("/sessions")
def list_sessions(db: Session = Depends(get_db), token: str = Depends(get_token)):
    sessions = db.query(TestSession).filter(TestSession.token == token).order_by(TestSession.created_at.desc()).all()
    return [{
        "id": s.id,
        "date": s.date,
        "source": s.source,
        "section": s.section,
        "total_questions": s.total_questions,
        "correct": s.correct,
        "accuracy": round(s.correct / s.total_questions * 100) if s.total_questions else 0,
        "created_at": s.created_at.isoformat(),
    } for s in sessions]


@app.get("/sessions/{session_id}")
def get_session(session_id: int, db: Session = Depends(get_db), token: str = Depends(get_token)):
    s = db.query(TestSession).filter(TestSession.id == session_id, TestSession.token == token).first()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    questions = db.query(QuestionResult).filter(QuestionResult.session_id == session_id).order_by(QuestionResult.question_number).all()
    return {
        "id": s.id, "date": s.date, "source": s.source, "section": s.section,
        "total_questions": s.total_questions, "correct": s.correct, "notes": s.notes,
        "accuracy": round(s.correct / s.total_questions * 100) if s.total_questions else 0,
        "questions": [{
            "id": q.id, "number": q.question_number, "topic": q.topic,
            "user_answer": q.user_answer, "correct_answer": q.correct_answer,
            "is_correct": q.is_correct, "time_spent": q.time_spent,
        } for q in questions],
    }


@app.delete("/sessions/{session_id}")
def delete_session(session_id: int, db: Session = Depends(get_db), token: str = Depends(get_token)):
    s = db.query(TestSession).filter(TestSession.id == session_id, TestSession.token == token).first()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    db.query(QuestionResult).filter(QuestionResult.session_id == session_id).delete()
    db.delete(s)
    db.commit()
    return {"ok": True}


@app.get("/analytics")
def get_analytics(db: Session = Depends(get_db), token: str = Depends(get_token)):
    sessions = db.query(TestSession).filter(TestSession.token == token).all()
    questions = db.query(QuestionResult).filter(QuestionResult.token == token).all()

    if not sessions:
        return {"total_questions": 0, "total_correct": 0, "accuracy": 0, "sessions_count": 0,
                "by_section": [], "weak_topics": [], "strong_topics": [], "trend": []}

    total_q = sum(s.total_questions for s in sessions)
    total_c = sum(s.correct for s in sessions)

    section_map = {}
    for s in sessions:
        sec = s.section or "Unknown"
        if sec not in section_map:
            section_map[sec] = {"questions": 0, "correct": 0}
        section_map[sec]["questions"] += s.total_questions
        section_map[sec]["correct"] += s.correct
    by_section = sorted([
        {"section": k, "questions": v["questions"], "correct": v["correct"],
         "accuracy": round(v["correct"] / v["questions"] * 100) if v["questions"] else 0}
        for k, v in section_map.items()
    ], key=lambda x: x["accuracy"])

    topic_map = {}
    for q in questions:
        t = q.topic or "Unknown"
        if t not in topic_map:
            topic_map[t] = {"questions": 0, "correct": 0}
        topic_map[t]["questions"] += 1
        if q.is_correct:
            topic_map[t]["correct"] += 1
    topic_list = sorted([
        {"topic": k, "questions": v["questions"], "correct": v["correct"],
         "accuracy": round(v["correct"] / v["questions"] * 100) if v["questions"] else 0}
        for k, v in topic_map.items() if v["questions"] >= 2
    ], key=lambda x: x["accuracy"])

    trend = sorted([{
        "session_id": s.id, "date": s.date, "section": s.section, "source": s.source,
        "accuracy": round(s.correct / s.total_questions * 100) if s.total_questions else 0,
        "correct": s.correct, "total": s.total_questions,
    } for s in sessions], key=lambda x: x["date"])

    return {
        "total_questions": total_q, "total_correct": total_c,
        "accuracy": round(total_c / total_q * 100) if total_q else 0,
        "sessions_count": len(sessions),
        "by_section": by_section,
        "weak_topics": topic_list[:8],
        "strong_topics": list(reversed(topic_list[-5:])),
        "trend": trend,
    }


@app.get("/insights")
def get_insights(db: Session = Depends(get_db), token: str = Depends(get_token)):
    analytics = get_analytics(db, token)
    if analytics["total_questions"] == 0:
        return {"insights": []}

    prompt = f"""A pre-med student is preparing for the MCAT. Based on their practice test performance below, write 4 specific, actionable study recommendations.

Overall: {analytics['accuracy']}% accuracy ({analytics['total_correct']}/{analytics['total_questions']} questions across {analytics['sessions_count']} tests)
Section breakdown: {json.dumps(analytics['by_section'])}
Weakest topics: {json.dumps(analytics['weak_topics'][:6])}
Strongest topics: {json.dumps(analytics['strong_topics'][:3])}

Return ONLY a JSON array of 4 strings. Each tip should be specific to their data, not generic advice.
["tip 1", "tip 2", "tip 3", "tip 4"]"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    text = response.content[0].text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return {"insights": json.loads(text)}
