from sqlalchemy import Column, Integer, String, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()

class TestSession(Base):
    __tablename__ = "test_sessions"
    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(64), index=True, default="")
    date = Column(String(20))
    source = Column(String(100), default="Unknown")
    section = Column(String(50), default="Unknown")
    total_questions = Column(Integer, default=0)
    correct = Column(Integer, default=0)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

class QuestionResult(Base):
    __tablename__ = "question_results"
    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(64), index=True, default="")
    session_id = Column(Integer, ForeignKey("test_sessions.id"))
    question_number = Column(Integer, nullable=True)
    topic = Column(String(200), default="Unknown")
    user_answer = Column(String(5), nullable=True)
    correct_answer = Column(String(5), nullable=True)
    is_correct = Column(Boolean, default=False)
    time_spent = Column(Integer, nullable=True)
