import sqlite3
import os
from core.utils import rprint

# The database will now store multiple tables, so we give it a more general name.
DATABASE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'output', 'main.db')

def init_db():
    """Initializes the database and creates tables if they don't exist."""
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    
    # Vocabulary table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vocabulary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word TEXT NOT NULL UNIQUE,
            pinyin TEXT,
            definition TEXT,
            example TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Translation history table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS translation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_video_name TEXT NOT NULL,
            video_path TEXT NOT NULL,
            subtitles_path TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Add session_id column to translation_history if it doesn't exist
    try:
        cursor.execute('ALTER TABLE translation_history ADD COLUMN session_id TEXT')
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    conn.commit()
    conn.close()


def clear_vocabulary():
    """Clears all entries from the vocabulary table."""
    try:
        init_db()
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM vocabulary")
        conn.commit()
        conn.close()
        rprint("[green]Vocabulary table cleared successfully.[/green]")
        return True
    except Exception as e:
        rprint(f"[bold red]Error clearing vocabulary table: {e}[/bold red]")
        return False

def add_word(word: str, pinyin: str, definition: str, example: str):
    """Adds a word and its details to the vocabulary table, preventing duplicates."""
    if not word:
        return False, "Word cannot be empty"
    
    try:
        init_db()
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT id FROM vocabulary WHERE word = ?", (word,))
        if cursor.fetchone():
            conn.close()
            rprint(f"[yellow]Word '{word}' already exists in vocabulary.[/yellow]")
            return True, f"Word '{word}' already exists."

        cursor.execute(
            "INSERT INTO vocabulary (word, pinyin, definition, example) VALUES (?, ?, ?, ?)",
            (word, pinyin, definition, example)
        )
        conn.commit()
        conn.close()
        
        rprint(f"[green]Word '{word}' with details added to vocabulary.[/green]")
        return True, f"Word '{word}' added."
            
    except Exception as e:
        rprint(f"[bold red]Error adding word to database: {e}[/bold red]")
        return False, f"Database error: {e}"

def get_all_words():
    """Retrieves all words and their details from the vocabulary table, ordered by newest first."""
    try:
        init_db()
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT word, pinyin, definition, example, timestamp FROM vocabulary ORDER BY timestamp DESC")
        words = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return words
    except Exception as e:
        rprint(f"[bold red]Error getting words from database: {e}[/bold red]")
        return []

# --- History Functions ---

def add_translation_to_history(original_video_name: str, video_path: str, subtitles_path: str, session_id: str = None):
    """Adds a new translation record to the history, associated with a session."""
    try:
        init_db()
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO translation_history (original_video_name, video_path, subtitles_path, session_id) VALUES (?, ?, ?, ?)",
            (original_video_name, video_path, subtitles_path, session_id)
        )
        conn.commit()
        conn.close()
        rprint(f"[green]Translation for '{original_video_name}' added to history for session '{session_id or 'guest'}'.[/green]")
        return True
    except Exception as e:
        rprint(f"[bold red]Error adding to translation history: {e}[/bold red]")
        return False

def get_translation_history(session_id: str = None):
    """Retrieves translation history records for a specific session."""
    try:
        init_db()
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if session_id:
            cursor.execute("SELECT id, original_video_name, timestamp FROM translation_history WHERE session_id = ? ORDER BY timestamp DESC", (session_id,))
        else:
            # Returns global history if no session_id is specified (for potential admin use)
            cursor.execute("SELECT id, original_video_name, timestamp FROM translation_history WHERE session_id IS NULL ORDER BY timestamp DESC")
            
        history = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return history
    except Exception as e:
        rprint(f"[bold red]Error getting translation history: {e}[/bold red]")
        return []

def get_history_entry(history_id: int, session_id: str = None):
    """Retrieves a single history entry by its ID, scoped to the session if a session_id is provided."""
    try:
        init_db()
        conn = sqlite3.connect(DATABASE_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if session_id:
            query = "SELECT * FROM translation_history WHERE id = ? AND session_id = ?"
            params = (history_id, session_id)
        else:
            query = "SELECT * FROM translation_history WHERE id = ? AND session_id IS NULL"
            params = (history_id,)
            
        cursor.execute(query, params)
        entry = cursor.fetchone()
        conn.close()
        return dict(entry) if entry else None
    except Exception as e:
        rprint(f"[bold red]Error getting history entry: {e}[/bold red]")
        return None