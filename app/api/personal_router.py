import logging
from datetime import datetime, date
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.auth_router import get_current_user
from app.models.personal import Note, PlannerTask
from app.models.document import Document

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/personal", tags=["personal"])


# Pydantic Schemas
class NoteCreate(BaseModel):
    title: str
    content: str = ""
    doc_id: Optional[str] = None


class NoteUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    doc_id: Optional[str] = None


class TaskCreate(BaseModel):
    title: str
    priority: str = "medium"  # low | medium | high
    due_date: Optional[str] = None  # ISO string or date string


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[str] = None
    is_completed: Optional[bool] = None


# ── Notes API ────────────────────────────────────────────────────────────────
@router.get("/notes")
async def get_notes(db: Session = Depends(get_db)):
    try:
        notes = db.query(Note).order_by(Note.updated_at.desc()).all()
        result = []
        for n in notes:
            # Fetch doc info if linked
            doc_name = None
            if n.doc_id:
                doc = db.query(Document).filter(Document.id == n.doc_id).first()
                if doc:
                    doc_name = doc.filename

            result.append({
                "id": n.id,
                "title": n.title,
                "content": n.content,
                "doc_id": n.doc_id,
                "doc_name": doc_name,
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "updated_at": n.updated_at.isoformat() if n.updated_at else None,
            })
        return {"notes": result}
    except Exception as e:
        logger.error(f"Error fetching notes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notes")
async def create_note(payload: NoteCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    try:
        note = Note(
            title=payload.title,
            content=payload.content,
            doc_id=payload.doc_id,
            user_id=current_user["id"],
        )
        db.add(note)
        db.commit()
        db.refresh(note)
        return {"note": {
            "id": note.id,
            "title": note.title,
            "content": note.content,
            "doc_id": note.doc_id,
            "created_at": note.created_at.isoformat() if note.created_at else None,
            "updated_at": note.updated_at.isoformat() if note.updated_at else None,
        }}
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating note: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/notes/{note_id}")
async def update_note(note_id: str, payload: NoteUpdate, db: Session = Depends(get_db)):
    try:
        note = db.query(Note).filter(Note.id == note_id).first()
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")

        if payload.title is not None:
            note.title = payload.title
        if payload.content is not None:
            note.content = payload.content
        if payload.doc_id is not None:
            note.doc_id = payload.doc_id if payload.doc_id != "" else None

        db.commit()
        db.refresh(note)
        return {"note": {
            "id": note.id,
            "title": note.title,
            "content": note.content,
            "doc_id": note.doc_id,
            "updated_at": note.updated_at.isoformat() if note.updated_at else None,
        }}
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating note {note_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/notes/{note_id}")
async def delete_note(note_id: str, db: Session = Depends(get_db)):
    try:
        note = db.query(Note).filter(Note.id == note_id).first()
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")
        db.delete(note)
        db.commit()
        return {"message": "Note deleted successfully", "id": note_id}
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting note {note_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Planner Tasks API ────────────────────────────────────────────────────────
@router.get("/planner")
async def get_tasks(db: Session = Depends(get_db)):
    try:
        tasks = db.query(PlannerTask).order_by(PlannerTask.created_at.desc()).all()
        result = [
            {
                "id": t.id,
                "title": t.title,
                "priority": t.priority,
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "is_completed": t.is_completed,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in tasks
        ]
        return {"tasks": result}
    except Exception as e:
        logger.error(f"Error fetching planner tasks: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/planner")
async def create_task(payload: TaskCreate, db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)):
    try:
        due = None
        if payload.due_date:
            try:
                due = datetime.fromisoformat(payload.due_date.replace("Z", "+00:00"))
            except ValueError:
                due = datetime.strptime(payload.due_date, "%Y-%m-%d")

        task = PlannerTask(
            title=payload.title,
            priority=payload.priority,
            due_date=due,
            user_id=current_user["id"],
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return {"task": {
            "id": task.id,
            "title": task.title,
            "priority": task.priority,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "is_completed": task.is_completed,
        }}
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/planner/{task_id}")
async def update_task(task_id: str, payload: TaskUpdate, db: Session = Depends(get_db)):
    try:
        task = db.query(PlannerTask).filter(PlannerTask.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if payload.title is not None:
            task.title = payload.title
        if payload.priority is not None:
            task.priority = payload.priority
        if payload.is_completed is not None:
            task.is_completed = payload.is_completed
        if payload.due_date is not None:
            if payload.due_date == "":
                task.due_date = None
            else:
                try:
                    task.due_date = datetime.fromisoformat(payload.due_date.replace("Z", "+00:00"))
                except ValueError:
                    task.due_date = datetime.strptime(payload.due_date, "%Y-%m-%d")

        db.commit()
        db.refresh(task)
        return {"task": {
            "id": task.id,
            "title": task.title,
            "priority": task.priority,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "is_completed": task.is_completed,
        }}
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/planner/{task_id}")
async def delete_task(task_id: str, db: Session = Depends(get_db)):
    try:
        task = db.query(PlannerTask).filter(PlannerTask.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        db.delete(task)
        db.commit()
        return {"message": "Task deleted successfully", "id": task_id}
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── AI Daily Briefing ─────────────────────────────────────────────────────────
@router.get("/briefing")
async def get_briefing(db: Session = Depends(get_db)):
    try:
        # 1. Total active files count
        total_docs = db.query(Document).filter(Document.status == 'completed').count()

        # 2. Open tasks count
        open_tasks = db.query(PlannerTask).filter(PlannerTask.is_completed == False).count()

        # 3. Fetch up to 3 upcoming tasks
        tasks = db.query(PlannerTask).filter(PlannerTask.is_completed == False).order_by(PlannerTask.priority.desc()).limit(3).all()
        task_list_str = ""
        if tasks:
            task_list_str = " Here are your top priority items:\n" + "\n".join([f"- [{t.priority.upper()}] {t.title}" for t in tasks])

        # 4. Generate quick assistant intro text
        brief = (
            f"Welcome back! You have {total_docs} analyzed documents in your enterprise workspace. "
            f"Currently, there are {open_tasks} uncompleted tasks on your planner checklist.{task_list_str}"
        )
        return {"briefing": brief}
    except Exception as e:
        logger.error(f"Error generating daily briefing: {e}")
        raise HTTPException(status_code=500, detail=str(e))
