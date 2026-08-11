"""
Enhanced Progress Tracker with Debug Logging
Replace your progress_tracker.py temporarily with this to see what's happening
"""

from typing import Dict, Optional, Callable
from dataclasses import dataclass
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class ProgressStage:
    """Represents a stage in the processing pipeline"""
    name: str
    weight: int
    description: str


class ProcessingProgress:
    """Tracks progress through the document processing pipeline"""

    STAGES = {
        'ingest': ProgressStage('ingest', 5, 'Loading document'),
        'parse': ProgressStage('parse', 25, 'Extracting content'),
        'classify': ProgressStage('classify', 10, 'Identifying document type'),
        'normalize': ProgressStage('normalize', 5, 'Normalizing tables'),
        'cleanup': ProgressStage('cleanup', 5, 'Cleaning text'),
        'analyze': ProgressStage('analyze', 40, 'Running AI analysis'),
        'finalize': ProgressStage('finalize', 10, 'Saving results'),
    }

    def __init__(self, document_id: str, update_callback: Optional[Callable] = None):
        self.document_id = document_id
        self.update_callback = update_callback
        self.current_stage: Optional[str] = None
        self.stage_progress: float = 0.0
        self.started_at = datetime.utcnow()
        self.stage_start_times: Dict[str, datetime] = {}

        # ✅ Debug logging
        print(f"🔧 ProcessingProgress created for {document_id}")
        print(f"   Callback registered: {update_callback is not None}")

    def start_stage(self, stage_name: str, substage: Optional[str] = None):
        """Mark the start of a processing stage"""
        if stage_name not in self.STAGES:
            logger.warning(f"Unknown stage: {stage_name}")
            return

        self.current_stage = stage_name
        self.stage_progress = 0.0
        self.stage_start_times[stage_name] = datetime.utcnow()

        stage = self.STAGES[stage_name]
        total_progress = self.get_total_progress()

        status_text = stage.description
        if substage:
            status_text = f"{stage.description} - {substage}"

        # ✅ Debug logging
        print(f"📊 [{self.document_id}] Stage started: {stage_name}")
        print(f"   Progress: {total_progress:.1f}% - {status_text}")
        print(f"   Callback exists: {self.update_callback is not None}")

        logger.info(f"📊 [{self.document_id}] Progress: {total_progress:.0f}% - {status_text}")

        if self.update_callback:
            print(f"   🔔 Calling callback...")
            try:
                self.update_callback(
                    document_id=self.document_id,
                    progress=total_progress,
                    stage=stage_name,
                    status=status_text
                )
                print(f"   ✅ Callback called successfully")
            except Exception as e:
                print(f"   ❌ Callback error: {e}")
                import traceback
                traceback.print_exc()
        else:
            print(f"   ⚠️ No callback to call!")

    def update_stage_progress(self, progress: float, substage: Optional[str] = None):
        """Update progress within the current stage"""
        if not self.current_stage:
            return

        self.stage_progress = max(0.0, min(100.0, progress))
        total_progress = self.get_total_progress()

        stage = self.STAGES[self.current_stage]
        status_text = stage.description
        if substage:
            status_text = f"{stage.description} - {substage}"

        # ✅ Debug logging
        print(f"📊 [{self.document_id}] Progress update: {total_progress:.1f}%")
        print(f"   Stage: {self.current_stage} ({self.stage_progress:.0f}%)")
        print(f"   Status: {status_text}")

        logger.debug(f"📊 [{self.document_id}] Progress: {total_progress:.0f}% - {status_text}")

        if self.update_callback:
            print(f"   🔔 Calling callback...")
            try:
                self.update_callback(
                    document_id=self.document_id,
                    progress=total_progress,
                    stage=self.current_stage,
                    status=status_text
                )
                print(f"   ✅ Callback called")
            except Exception as e:
                print(f"   ❌ Callback error: {e}")

    def complete_stage(self):
        """Mark current stage as 100% complete"""
        if self.current_stage:
            self.stage_progress = 100.0
            total_progress = self.get_total_progress()

            elapsed = (datetime.utcnow() - self.stage_start_times[self.current_stage]).total_seconds()

            print(f"✅ [{self.document_id}] Stage '{self.current_stage}' complete ({elapsed:.1f}s)")
            print(f"   Total progress: {total_progress:.1f}%")

            logger.info(
                f"✅ [{self.document_id}] Stage '{self.current_stage}' complete "
                f"({elapsed:.1f}s) - Total: {total_progress:.0f}%"
            )

    def get_total_progress(self) -> float:
        """Calculate overall progress percentage (0-100)"""
        if not self.current_stage:
            return 0.0

        total_weight = sum(stage.weight for stage in self.STAGES.values())
        completed_weight = 0

        stage_order = list(self.STAGES.keys())
        current_index = stage_order.index(self.current_stage)

        for i in range(current_index):
            stage_name = stage_order[i]
            completed_weight += self.STAGES[stage_name].weight

        current_stage_weight = self.STAGES[self.current_stage].weight
        current_contribution = (current_stage_weight * self.stage_progress) / 100.0

        total_progress = ((completed_weight + current_contribution) / total_weight) * 100.0

        return min(100.0, max(0.0, total_progress))

    def get_elapsed_time(self) -> float:
        """Get total elapsed time in seconds"""
        return (datetime.utcnow() - self.started_at).total_seconds()

    def get_eta(self) -> Optional[float]:
        """Estimate time remaining in seconds"""
        progress = self.get_total_progress()
        if progress < 5:
            return None

        elapsed = self.get_elapsed_time()
        estimated_total = (elapsed / progress) * 100.0
        return max(0, estimated_total - elapsed)


class ProgressTracker:
    """Global progress tracker managing multiple concurrent document processing tasks"""

    def __init__(self):
        self.active_tasks: Dict[str, ProcessingProgress] = {}
        self.update_callbacks: list = []
        print("🔧 ProgressTracker initialized")

    def register_callback(self, callback: Callable):
        """Register a callback for progress updates"""
        self.update_callbacks.append(callback)
        print(f"✅ Callback registered (total: {len(self.update_callbacks)})")
        print(f"   Callback: {callback}")

    def start_processing(self, document_id: str) -> ProcessingProgress:
        """Start tracking a new document processing task"""
        print(f"🚀 Starting progress tracking for: {document_id}")
        print(f"   Active callbacks: {len(self.update_callbacks)}")

        def update_callback(**kwargs):
            print(f"   📡 Broadcasting update for {kwargs.get('document_id')}")
            print(f"      Progress: {kwargs.get('progress'):.1f}%")
            print(f"      Stage: {kwargs.get('stage')}")
            print(f"      Status: {kwargs.get('status')}")

            for i, cb in enumerate(self.update_callbacks):
                try:
                    print(f"      🔔 Calling callback #{i + 1}...")
                    cb(**kwargs)
                    print(f"      ✅ Callback #{i + 1} succeeded")
                except Exception as e:
                    print(f"      ❌ Callback #{i + 1} failed: {e}")
                    logger.error(f"Progress callback error: {e}")

        progress = ProcessingProgress(document_id, update_callback)
        self.active_tasks[document_id] = progress
        logger.info(f"🚀 Started tracking progress for: {document_id}")
        return progress

    def get_progress(self, document_id: str) -> Optional[ProcessingProgress]:
        """Get progress tracker for a document"""
        return self.active_tasks.get(document_id)

    def finish_processing(self, document_id: str):
        """Mark document processing as complete and cleanup"""
        if document_id in self.active_tasks:
            progress = self.active_tasks[document_id]
            elapsed = progress.get_elapsed_time()

            print(f"✅ Finishing progress tracking: {document_id}")
            print(f"   Total time: {elapsed:.1f}s")

            logger.info(f"✅ Completed processing: {document_id} (total: {elapsed:.1f}s)")

            # Send 100% completion
            for cb in self.update_callbacks:
                try:
                    cb(
                        document_id=document_id,
                        progress=100.0,
                        stage='complete',
                        status='Analysis complete'
                    )
                except Exception as e:
                    logger.error(f"Progress callback error: {e}")

            del self.active_tasks[document_id]

    def get_all_active(self) -> Dict[str, Dict]:
        """Get status of all active processing tasks"""
        return {
            doc_id: {
                'progress': progress.get_total_progress(),
                'stage': progress.current_stage,
                'elapsed': progress.get_elapsed_time(),
                'eta': progress.get_eta(),
            }
            for doc_id, progress in self.active_tasks.items()
        }


# Global singleton instance
_global_tracker: Optional[ProgressTracker] = None


def get_progress_tracker() -> ProgressTracker:
    """Get or create the global progress tracker instance"""
    global _global_tracker
    if _global_tracker is None:
        print("🔧 Creating global progress tracker...")
        _global_tracker = ProgressTracker()
    return _global_tracker