@echo off
echo ============================================================
echo  Smart Attendance System v8.0 — Windows Setup
echo ============================================================

echo [1/6] Creating .env file from example...
if not exist .env (
    copy .env.example .env
    echo   Created .env — EDIT IT with your passwords before starting!
) else (
    echo   .env already exists — skipping
)

echo.
echo [2/6] Installing core packages...
pip install opencv-contrib-python numpy Pillow
pip install scikit-learn pandas
pip install python-dotenv

echo.
echo [3/6] Installing database + API packages...
pip install psycopg2-binary
pip install fastapi "uvicorn[standard]" PyJWT pydantic python-multipart

echo.
echo [4/6] Installing MediaPipe (skeleton liveness)...
pip install mediapipe

echo.
echo [5/6] Installing dlib (takes 5-10 minutes)...
pip install cmake
pip install dlib
pip install face-recognition

echo.
echo [6/6] Installing optional packages...
pip install openpyxl

echo.
echo ============================================================
echo  Setup COMPLETE!
echo.
echo  Next steps:
echo  1. Edit .env with your PostgreSQL password + credentials
echo  2. Ensure PostgreSQL is running
echo  3. Create database: psql -U postgres -c "CREATE DATABASE attendance_db;"
echo  4. Run: python main.py
echo  5. Choose [1] Enrol, [2] Train, [3] Session OR [4] API+Frontend
echo  6. Dashboard: http://localhost:8000/app
echo ============================================================
pause
