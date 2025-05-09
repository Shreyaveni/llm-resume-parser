import os
import fitz
import json
import ast
import re
import sqlite3
import requests
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, make_response
from dotenv import load_dotenv
import google.generativeai as genai
from werkzeug.security import generate_password_hash, check_password_hash
from flask import send_from_directory
import threading
import atexit
load_dotenv()

def cleanup():
    for thread in threading.enumerate():
        if thread.daemon:
            thread.join(timeout=1.0)

atexit.register(cleanup)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your-secret-key")

# Configure Gemini API
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
model = genai.GenerativeModel("models/gemini-1.5-pro")

# Database setup
DATABASE = 'resumes.db'

def get_db():
    conn = sqlite3.connect(DATABASE)
    return conn

def init_db():
    with get_db() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS resumes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        full_name TEXT,
                        contact_number TEXT,
                        email_address TEXT,
                        location TEXT,
                        technical_skills TEXT,
                        non_technical_skills TEXT,
                        education TEXT,
                        work_experience TEXT,
                        certifications TEXT,
                        languages TEXT,
                        suggested_resume_category TEXT,
                        recommended_job_roles TEXT,
                        resume_score INTEGER,
                        resume_feedback TEXT,
                        filename TEXT,
                        FOREIGN KEY (user_id) REFERENCES users(id))''')

def extract_text_from_pdf(pdf_path):
    text = ""
    with fitz.open(pdf_path) as doc:
        for page in doc:
            text += page.get_text()
    return text

def resumes_details(resume_text):
    prompt = f"""
    Extract the following details from this resume and return ONLY valid JSON (no explanation or markdown formatting):
    {{
        "full_name": "...",
        "contact_number": "...",
        "email_address": "...",
        "Address": "...",
        "technical_skills": ["..."],
        "non_technical_skills": ["..."],
        "education": ["..."],
        "work_experience": ["..."],
        "certifications": ["..."],
        "languages": ["..."],
        "suggested_resume_category": "...",
        "recommended_job_roles": ["..."]
    }}

    Resume Text:
    {resume_text}
    """

    response = model.generate_content(prompt).text.strip()
    response = response.strip("`").strip()
    match = re.search(r"{.*}", response, re.DOTALL)
    if not match:
        raise ValueError("No valid JSON found in AI response.")
    json_text = match.group(0)
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(json_text)
        except Exception as e:
            raise ValueError(f"Failed to parse JSON: {e}")

import json

def resume_feedback_score(resume_json, resume_text):
    prompt = f"""
    You are an expert resume reviewer and career advisor.

    Analyze the following resume and rate it realistically out of 10 based on the following detailed criteria:

    **Core Evaluation Factors**
    - Content quality and relevance of skills to the job market or target roles
    - Formatting and structure (alignment, spacing, bullet points, headers, section order)
    - Completeness of important sections:
      - Full name
      - Contact number
      - Email address
      - Address
      - Technical skills
      - Non-technical skills
      - Education
      - Work experience
      - Certifications
      - Languages
    - Grammar, clarity, spelling, and consistency
    Highlight **missing or weak fields** from the structured data (resume_json) and explain how filling them will improve the resume.

    **Advanced Evaluation Factors**
    - Whether accomplishments are shown instead of just responsibilities in work experience
    - Visual appeal and readability (easy to scan for key details)
    - Relevance to a target job role (if visible or implied in resume)
    - Mention of personal or academic projects with links (e.g., GitHub, Behance)
    - Presence of soft skills or extra-curricular activities
    - Consistent use of verb tense and writing style
    - A clear objective or professional summary (optional but beneficial)

    Avoid suggesting replacements or expansions for generic technical terms like "DSA", "OOP", "OS", or tools like "VS Code", "Jira", unless they are completely missing from the resume.

    Then give a **realistic score** out of 100 and **exactly 5 concise suggestions** (each suggestion should be no more than 2 lines).

    Resume JSON:
    {json.dumps(resume_json, indent=2)}

    Resume Text:
    {resume_text}

    Format your response exactly as:
    Score: X/100
    Suggestions:
    1. First suggestion (max 2 lines)
    2. Second suggestion (max 2 lines)
    3. Third suggestion (max 2 lines)
    4. Fourth suggestion (max 2 lines)
    5. Fifth suggestion (max 2 lines)
    """

    response = model.generate_content(prompt).text.strip()
    lines = response.splitlines()
    score_line = next((line for line in lines if line.strip().startswith("Score:")), "Score: 0/100")
    suggestions_start = [i for i, line in enumerate(lines) if line.strip().startswith("Suggestions:")]
    
    if suggestions_start:
        # Extract only the next 3 suggestions
        suggestion_lines = []
        i = suggestions_start[0] + 1
        while len(suggestion_lines) < 4 and i < len(lines):
            line = lines[i].strip()
            if line and line[0].isdigit() and line[1] == '.':
                suggestion_lines.append(line)
            i += 1
    else:
        suggestion_lines = ["1. Not enough data to evaluate.", "2. Not enough data to evaluate.", "3. Not enough data to evaluate."]

    score = score_line.replace("Score:", "").replace("/100", "").strip()
    suggestions = "\n".join(suggestion_lines).strip()
    return score, suggestions

def fetch_job_recommendations(job_role):
    
        keywords = job_role
        print(f"[DEBUG] Search keywords: {keywords}")

        url = "https://jooble.org/api/"
        api_key = os.getenv("JOOBLE_API_KEY")

        if not api_key:
            print("[ERROR] Jooble API Key not found. Check your .env file.")
            return []

        headers = {
            "Content-Type": "application/json"
        }

        body = {
            "keywords": keywords,
            "location": "India"
        }

        try:
            response = requests.post(url + api_key, headers=headers, json=body)
            data = response.json()

            job_listings = []
            for job in data.get("jobs", [])[:3]: 
                job_listings.append({
                    "company": job.get("company", "Unknown Company"),
                    "title": job.get("title", "Unknown Title"),
                    "location": job.get("location", "Unknown Location"),
                    "url": job.get("link", "#")
                })

            return job_listings

        except Exception as e:
            print(f"Job API error: {e}")
            return []

    
# Routes
@app.route('/')
def index():
    if 'username' not in session:
        return redirect(url_for('login'))  

    resumes = []
    with get_db() as db:
        user = db.execute('SELECT id FROM users WHERE username = ?', (session['username'],)).fetchone()
        if user:
            user_id = user[0]
            rows = db.execute('SELECT filename, resume_score FROM resumes WHERE user_id = ?', (user_id,)).fetchall()
            for row in rows:
                filename, score = row
                resumes.append({
                    "filename": filename,
                    "score": score
                })

    resume_error = session.pop('resume_error', None)
    return render_template('index.html', username=session['username'], resumes=resumes, resume_error=resume_error)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        with get_db() as db:
            user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
            if user and check_password_hash(user[2], password):
                session['username'] = username
                return redirect(url_for('index'))
            else:
                return 'Invalid credentials. Please try again.'
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        hashed_password = generate_password_hash(password)
        try:
            with get_db() as db:
                db.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, hashed_password))
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            return 'Username already taken. Please choose another one.'
    return render_template('signup.html')

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory('uploads', filename)

@app.route('/upload_resume', methods=['POST'])
def upload_resume():
    if 'resume' not in request.files:
        return jsonify({"error": "No file uploaded."})

    resume_file = request.files['resume']
    if resume_file.filename == '':
        return jsonify({"error": "No selected file."})

    filepath = os.path.join('uploads', resume_file.filename)
    os.makedirs('uploads', exist_ok=True)
    resume_file.save(filepath)

    try:
        text = extract_text_from_pdf(filepath)
        data = resumes_details(text)

        required_fields = [
            'full_name', 'contact_number', 'email_address', 'technical_skills',
            'non_technical_skills', 'education', 
            'certifications', 'languages'
        ]

        missing_fields = [
            field.replace('_', ' ').title()
            for field in required_fields
            if not data.get(field) or (isinstance(data.get(field), list) and not any(data.get(field)))
        ]

        if missing_fields:
            missing_str = ', '.join(missing_fields)
            session['resume_error'] = (
                f"Your resume does not contain the required section(s): {missing_str}. "
                "Please add these section(s) and try again."
            )
            return redirect(url_for('index'))

        score, suggestions = resume_feedback_score(data, text)

        def safe_join(field):
            return ', '.join(
                str(item) if isinstance(item, str) else json.dumps(item)
                for item in data.get(field, [])
            )

        with get_db() as db:
            user = db.execute('SELECT id FROM users WHERE username = ?', (session['username'],)).fetchone()
            if not user:
                return jsonify({"error": "User session expired. Please log in again."})
            user_id = user[0]

            db.execute('''INSERT INTO resumes (
                            user_id, full_name, contact_number, email_address, location,
                            technical_skills, non_technical_skills, education, work_experience,
                            certifications, languages, suggested_resume_category,
                            recommended_job_roles, resume_score, resume_feedback, filename)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (
                            user_id,
                            data.get('full_name', ''),
                            data.get('contact_number', ''),
                            data.get('email_address', ''),
                            data.get('location', ''),
                            safe_join('technical_skills'),
                            safe_join('non_technical_skills'),
                            safe_join('education'),
                            safe_join('work_experience'),
                            safe_join('certifications'),
                            safe_join('languages'),
                            data.get('suggested_resume_category', ''),
                            safe_join('recommended_job_roles'),
                            score,
                            suggestions,
                            resume_file.filename
                        ))

        job_role = data.get('recommended_job_roles', [''])[0]
        job_recommendations = fetch_job_recommendations(job_role)
        # print(f"[DEBUG] Number of job recommendations: {len(job_recommendations)}")
       
        session['parsed_data'] = data
        session['resume_score'] = score
        session['resume_feedback'] = suggestions
        session['job_recommendations'] = job_recommendations
        session['resume_filename'] = resume_file.filename

        return redirect(url_for('parsed_result'))

    except Exception as e:
        return jsonify({"error": f"Processing failed: {str(e)}"})

@app.route('/parsed_result')
def parsed_result():
    data = session.get('parsed_data', {})
    job_recommendations = session.get('job_recommendations', [])

    return render_template('parsed_result.html', **data, job_recommendations=job_recommendations)

@app.route('/suggestions')
def suggestions():
    score = session.get('resume_score')
    feedback = session.get('resume_feedback')
    filename = session.get('resume_filename')  

    return render_template('suggestions.html',
                           resume_score=score,
                           resume_feedback=feedback,
                           resume_file=filename)

@app.route('/download_feedback', methods=['POST'])
def download_feedback():
    score = request.form.get('score', 'N/A')
    feedback = request.form.get('feedback', 'No suggestions available.')
    content = f"Resume Score: {score}/10\n\nSuggestions:\n{feedback}"
    response = make_response(content)
    response.headers['Content-Disposition'] = 'attachment; filename=resume_feedback.txt'
    response.headers['Content-Type'] = 'text/plain'
    return response

def add_filename_column():
    with get_db() as db:
        try:
            db.execute("ALTER TABLE resumes ADD COLUMN filename TEXT")
            db.commit()
            print("Added filename column to resumes table.")
        except sqlite3.OperationalError as e:
            print("Migration error:", e)


if __name__ == '__main__':
    init_db()
    app.run(host="0.0.0.0", port=8001, debug=True, use_reloader=False)