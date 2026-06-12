# =============================================================
# timetable_seed.py  —  Smart Attendance System
#
# Seeds the complete college timetable into the database:
#   - 8 departments: CSE, AIDS, CSBS, ECE, EEE, MECH, CIVIL, BME
#   - Semesters 1-8 (Years 1-4)
#   - Sections A, B, C
#   - 50 Faculty members (FAC001-FAC050)
#   - student_timetable + staff_timetable + courses + faculty tables
#
# Run standalone: python timetable_seed.py
# Or import: from timetable_seed import seed_all_timetables; seed_all_timetables()
# =============================================================

import os, sqlite3, logging, random
from contextlib import contextmanager
import config

log = logging.getLogger(__name__)
DB_PATH = os.path.join(config.BASE_DIR, "attendance.db")

@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# =============================================================
# COURSE DATA — all 8 departments, semesters 1-8
# =============================================================

COURSES = {
    "CSE": {
        1: [("HS3152","Professional English I","core"),("MA3151","Engineering Mathematics I","core"),
            ("PH3151","Engineering Physics","core"),("CY3151","Engineering Chemistry","core"),
            ("GE3151","Problem Solving & Python Programming","core"),("GE3152","Engineering Graphics","core"),
            ("GE3161","Python Programming Lab","lab"),("BS3161","Physics & Chemistry Laboratory","lab")],
        2: [("HS3252","Professional English II","core"),("MA3251","Engineering Mathematics II","core"),
            ("PH3256","Physics for Information Science","core"),("BE3251","Basic Electrical & Electronics Engg","core"),
            ("GE3251","Environmental Science & Sustainability","core"),
            ("GE3271","Engineering Practices Laboratory","lab"),("GE3272","Communication Laboratory","lab")],
        3: [("MA3354","Discrete Mathematics","core"),("CS3351","Digital Principles & Computer Organization","core"),
            ("CS3352","Foundations of Data Science","core"),("CS3301","Data Structures","core"),
            ("CS3361","Data Structures Laboratory","lab"),("CS3362","Object Oriented Programming Laboratory","lab")],
        4: [("MA3402","Probability & Queueing Theory","core"),("CS3451","Design & Analysis of Algorithms","core"),
            ("CS3452","Database Management Systems","core"),("CS3491","Artificial Intelligence","core"),
            ("CS3461","Database Management Systems Laboratory","lab"),("CS3462","Operating Systems Laboratory","lab")],
        5: [("CS3591","Computer Networks","core"),("CS3501","Theory of Computation","core"),
            ("CS3551","Distributed Computing","core"),("CS3541","Operating Systems","core"),
            ("CS3511","Networks Laboratory","lab"),("CS3512","OS Laboratory","lab")],
        6: [("CS3651","Internet Programming","core"),("CS3691","AI & Machine Learning","core"),
            ("CS3601","Mobile Computing","core"),("CS3661","Internet Programming Laboratory","lab"),
            ("CS3662","Machine Learning Laboratory","lab"),("PE1","Professional Elective I","elective")],
        7: [("CS3791","Cloud Computing","core"),("CS3792","Information Security","core"),
            ("CS3711","Cloud Computing Laboratory","lab"),("CS3712","Security Laboratory","lab"),
            ("CS3713","Project Phase I","project"),("PE2","Professional Elective II","elective"),
            ("PE3","Professional Elective III","elective")],
        8: [("CS3811","Project Work","project"),("PE4","Professional Elective IV","elective"),
            ("PE5","Professional Elective V","elective")],
    },
    "AIDS": {
        1: [("HS3152","Professional English I","core"),("MA3151","Engineering Mathematics I","core"),
            ("PH3151","Engineering Physics","core"),("CY3151","Engineering Chemistry","core"),
            ("GE3151","Problem Solving & Python Programming","core"),("GE3152","Engineering Graphics","core"),
            ("GE3161","Python Programming Lab","lab"),("BS3161","Physics & Chemistry Laboratory","lab")],
        2: [("HS3252","Professional English II","core"),("MA3251","Engineering Mathematics II","core"),
            ("PH3256","Physics for Information Science","core"),("BE3251","Basic Electrical & Electronics Engg","core"),
            ("GE3251","Environmental Science & Sustainability","core"),
            ("GE3271","Engineering Practices Laboratory","lab"),("GE3272","Communication Laboratory","lab")],
        3: [("MA3354","Discrete Mathematics","core"),("AD3351","Data Exploration & Visualization","core"),
            ("AD3352","Programming for Data Science","core"),("CS3351","Digital Principles & Computer Organization","core"),
            ("AD3361","Data Science Laboratory","lab"),("CS3361","Digital Systems Laboratory","lab")],
        4: [("MA3402","Probability & Statistics","core"),("AD3451","Machine Learning","core"),
            ("AD3452","Database Management Systems","core"),("AD3453","Artificial Intelligence","core"),
            ("AD3461","Machine Learning Laboratory","lab"),("AD3462","DBMS Laboratory","lab")],
        5: [("AD3501","Deep Learning","core"),("AD3502","Data Mining","core"),
            ("AD3503","Big Data Analytics","core"),("AD3511","Deep Learning Laboratory","lab"),
            ("AD3512","Data Mining Laboratory","lab"),("PE1","Professional Elective I","elective")],
        6: [("AD3601","Natural Language Processing","core"),("AD3602","Computer Vision","core"),
            ("AD3603","Reinforcement Learning","core"),("AD3611","AI & NLP Laboratory","lab"),
            ("PE2","Professional Elective II","elective"),("OE","Open Elective","elective")],
        7: [("AD3701","Edge AI","core"),("AD3711","Project Phase I","project"),
            ("PE3","Professional Elective III","elective"),("PE4","Professional Elective IV","elective"),
            ("OE2","Open Elective II","elective")],
        8: [("AD3811","Project Work","project"),("PE5","Professional Elective V","elective")],
    },
    "CSBS": {
        1: [("HS3152","Professional English I","core"),("MA3151","Engineering Mathematics I","core"),
            ("PH3151","Engineering Physics","core"),("CY3151","Engineering Chemistry","core"),
            ("GE3151","Problem Solving & Python Programming","core"),("GE3152","Engineering Graphics","core"),
            ("GE3161","Python Programming Lab","lab"),("BS3161","Physics & Chemistry Laboratory","lab")],
        2: [("HS3252","Professional English II","core"),("MA3251","Engineering Mathematics II","core"),
            ("PH3256","Physics for Information Science","core"),("BE3251","Basic Electrical & Electronics Engg","core"),
            ("GE3251","Environmental Science & Sustainability","core"),
            ("GE3271","Engineering Practices Laboratory","lab"),("GE3272","Communication Laboratory","lab")],
        3: [("MA3354","Discrete Mathematics","core"),("CB3351","Business Economics","core"),
            ("CB3352","Data Structures","core"),("CS3351","Digital Principles & Computer Organization","core"),
            ("CB3361","Data Structures Laboratory","lab"),("CB3362","Business Analytics Laboratory","lab")],
        4: [("MA3402","Probability & Statistics","core"),("CB3451","Database Management Systems","core"),
            ("CB3452","Business Analytics","core"),("CB3453","Software Engineering","core"),
            ("CB3461","Database Systems Laboratory","lab"),("CB3462","Analytics Laboratory","lab")],
        5: [("CB3501","Web Technologies","core"),("CB3502","Financial Management","core"),
            ("CB3503","Enterprise Resource Planning","core"),("CB3511","Web Technology Laboratory","lab"),
            ("CB3512","ERP Laboratory","lab"),("PE1","Professional Elective I","elective")],
        6: [("CB3601","Cloud Computing","core"),("CB3602","AI for Business","core"),
            ("CB3603","Digital Marketing Analytics","core"),("CB3611","Cloud Computing Laboratory","lab"),
            ("PE2","Professional Elective II","elective"),("OE","Open Elective","elective")],
        7: [("CB3701","Enterprise Systems","core"),("CB3711","Project Phase I","project"),
            ("PE3","Professional Elective III","elective"),("PE4","Professional Elective IV","elective"),
            ("OE2","Open Elective II","elective")],
        8: [("CB3811","Project Work","project"),("PE5","Professional Elective V","elective")],
    },
    "ECE": {
        1: [("HS3152","Professional English I","core"),("MA3151","Engineering Mathematics I","core"),
            ("PH3151","Engineering Physics","core"),("CY3151","Engineering Chemistry","core"),
            ("GE3151","Problem Solving & Python Programming","core"),("GE3152","Engineering Graphics","core"),
            ("GE3161","Python Programming Laboratory","lab"),("BS3161","Physics & Chemistry Laboratory","lab")],
        2: [("HS3252","Professional English II","core"),("MA3251","Engineering Mathematics II","core"),
            ("PH3256","Physics for Information Science","core"),("BE3251","Basic Electrical & Electronics Engg","core"),
            ("GE3251","Environmental Science & Sustainability","core"),
            ("GE3271","Engineering Practices Laboratory","lab"),("GE3272","Communication Laboratory","lab")],
        3: [("MA3354","Discrete Mathematics","core"),("EC3351","Electronic Devices & Circuits","core"),
            ("EC3352","Signals & Systems","core"),("CS3351","Digital Principles & Computer Organization","core"),
            ("EC3361","Electronic Devices Laboratory","lab"),("EC3362","Digital Systems Laboratory","lab")],
        4: [("EC3451","Linear Integrated Circuits","core"),("EC3452","Digital Signal Processing","core"),
            ("EC3453","Control Systems","core"),("EC3461","Linear Integrated Circuits Laboratory","lab"),
            ("EC3462","Digital Signal Processing Laboratory","lab")],
        5: [("EC3501","Communication Systems","core"),("EC3502","Microprocessors & Microcontrollers","core"),
            ("EC3503","VLSI Design","core"),("EC3511","Microprocessor Laboratory","lab"),
            ("EC3512","Communication Systems Laboratory","lab"),("PE1","Professional Elective I","elective")],
        6: [("EC3601","Wireless Communication","core"),("EC3602","Embedded Systems","core"),
            ("EC3603","Optical Communication","core"),("EC3611","Embedded Systems Laboratory","lab"),
            ("PE2","Professional Elective II","elective"),("OE","Open Elective","elective")],
        7: [("EC3701","Internet of Things","core"),("EC3711","Project Phase I","project"),
            ("PE3","Professional Elective III","elective"),("PE4","Professional Elective IV","elective"),
            ("OE2","Open Elective II","elective")],
        8: [("EC3811","Project Work","project"),("PE5","Professional Elective V","elective")],
    },
    "EEE": {
        1: [("HS3152","Professional English I","core"),("MA3151","Engineering Mathematics I","core"),
            ("PH3151","Engineering Physics","core"),("CY3151","Engineering Chemistry","core"),
            ("GE3151","Problem Solving & Python Programming","core"),("GE3152","Engineering Graphics","core"),
            ("GE3161","Python Programming Laboratory","lab"),("BS3161","Physics & Chemistry Laboratory","lab")],
        2: [("HS3252","Professional English II","core"),("MA3251","Engineering Mathematics II","core"),
            ("PH3256","Physics for Information Science","core"),("BE3251","Basic Electrical & Electronics Engg","core"),
            ("GE3251","Environmental Science & Sustainability","core"),
            ("GE3271","Engineering Practices Laboratory","lab"),("GE3272","Communication Laboratory","lab")],
        3: [("MA3354","Discrete Mathematics","core"),("EE3351","Electric Circuit Analysis","core"),
            ("EE3352","Electromagnetic Fields","core"),("EE3361","Electric Circuits Laboratory","lab"),
            ("EE3362","Electrical Machines Laboratory","lab")],
        4: [("EE3451","Power Systems I","core"),("EE3452","Electrical Machines I","core"),
            ("EE3453","Power Electronics","core"),("EE3461","Power Electronics Laboratory","lab"),
            ("EE3462","Electrical Machines Laboratory II","lab")],
        5: [("EE3501","Control Systems","core"),("EE3502","Electrical Machines II","core"),
            ("EE3503","Power Systems II","core"),("EE3511","Control Systems Laboratory","lab"),
            ("EE3512","Power Systems Laboratory","lab"),("PE1","Professional Elective I","elective")],
        6: [("EE3601","Power System Analysis","core"),("EE3602","Microprocessors & Microcontrollers","core"),
            ("EE3603","Renewable Energy Systems","core"),("EE3611","Microprocessor Laboratory","lab"),
            ("PE2","Professional Elective II","elective"),("OE","Open Elective","elective")],
        7: [("EE3701","Electric Drives","core"),("EE3711","Project Phase I","project"),
            ("PE3","Professional Elective III","elective"),("PE4","Professional Elective IV","elective"),
            ("OE2","Open Elective II","elective")],
        8: [("EE3811","Project Work","project"),("PE5","Professional Elective V","elective")],
    },
    "MECH": {
        1: [("HS3152","Professional English I","core"),("MA3151","Engineering Mathematics I","core"),
            ("PH3151","Engineering Physics","core"),("CY3151","Engineering Chemistry","core"),
            ("GE3151","Problem Solving & Python Programming","core"),("GE3152","Engineering Graphics","core"),
            ("GE3161","Python Programming Laboratory","lab"),("BS3161","Physics & Chemistry Laboratory","lab")],
        2: [("HS3252","Professional English II","core"),("MA3251","Engineering Mathematics II","core"),
            ("PH3256","Physics for Information Science","core"),("BE3251","Basic Electrical & Electronics Engg","core"),
            ("GE3251","Environmental Science & Sustainability","core"),
            ("GE3271","Engineering Practices Laboratory","lab"),("GE3272","Communication Laboratory","lab")],
        3: [("MA3351","Transforms & Partial Differential Equations","core"),
            ("ME3351","Engineering Mechanics","core"),("ME3352","Manufacturing Technology I","core"),
            ("ME3361","Manufacturing Technology Laboratory","lab"),("ME3362","Engineering Mechanics Laboratory","lab")],
        4: [("ME3451","Fluid Mechanics & Machinery","core"),("ME3452","Manufacturing Technology II","core"),
            ("ME3453","Thermodynamics","core"),("ME3461","Fluid Mechanics Laboratory","lab"),
            ("ME3462","Thermal Engineering Laboratory","lab")],
        5: [("ME3501","Design of Machine Elements","core"),("ME3502","Heat Transfer","core"),
            ("ME3503","Dynamics of Machines","core"),("ME3511","Machine Design Laboratory","lab"),
            ("ME3512","Dynamics Laboratory","lab"),("PE1","Professional Elective I","elective")],
        6: [("ME3601","Automobile Engineering","core"),("ME3602","Refrigeration & Air Conditioning","core"),
            ("ME3603","Computer Aided Design","core"),("ME3611","CAD Laboratory","lab"),
            ("PE2","Professional Elective II","elective"),("OE","Open Elective","elective")],
        7: [("ME3701","Robotics & Automation","core"),("ME3711","Project Phase I","project"),
            ("PE3","Professional Elective III","elective"),("PE4","Professional Elective IV","elective"),
            ("OE2","Open Elective II","elective")],
        8: [("ME3811","Project Work","project"),("PE5","Professional Elective V","elective")],
    },
    "CIVIL": {
        1: [("HS3152","Professional English I","core"),("MA3151","Engineering Mathematics I","core"),
            ("PH3151","Engineering Physics","core"),("CY3151","Engineering Chemistry","core"),
            ("GE3151","Problem Solving & Python Programming","core"),("GE3152","Engineering Graphics","core"),
            ("GE3161","Python Programming Laboratory","lab"),("BS3161","Physics & Chemistry Laboratory","lab")],
        2: [("HS3252","Professional English II","core"),("MA3251","Engineering Mathematics II","core"),
            ("PH3256","Physics for Information Science","core"),("BE3251","Basic Electrical & Electronics Engg","core"),
            ("GE3251","Environmental Science & Sustainability","core"),
            ("GE3271","Engineering Practices Laboratory","lab"),("GE3272","Communication Laboratory","lab")],
        3: [("MA3351","Transforms & Partial Differential Equations","core"),
            ("CE3351","Engineering Mechanics","core"),("CE3352","Strength of Materials","core"),
            ("CE3361","Strength of Materials Laboratory","lab"),("CE3362","Surveying Laboratory","lab")],
        4: [("CE3451","Fluid Mechanics","core"),("CE3452","Structural Analysis I","core"),
            ("CE3453","Geotechnical Engineering I","core"),("CE3461","Fluid Mechanics Laboratory","lab"),
            ("CE3462","Geotechnical Engineering Laboratory","lab")],
        5: [("CE3501","Structural Analysis II","core"),("CE3502","Environmental Engineering","core"),
            ("CE3503","Transportation Engineering","core"),("CE3511","Environmental Engineering Laboratory","lab"),
            ("CE3512","Highway Engineering Laboratory","lab"),("PE1","Professional Elective I","elective")],
        6: [("CE3601","Design of Reinforced Concrete Elements","core"),("CE3602","Water Resources Engineering","core"),
            ("CE3603","Construction Planning & Management","core"),("CE3611","Structural Design Laboratory","lab"),
            ("PE2","Professional Elective II","elective"),("OE","Open Elective","elective")],
        7: [("CE3701","Design of Steel Structures","core"),("CE3711","Project Phase I","project"),
            ("PE3","Professional Elective III","elective"),("PE4","Professional Elective IV","elective"),
            ("OE2","Open Elective II","elective")],
        8: [("CE3811","Project Work","project"),("PE5","Professional Elective V","elective")],
    },
    "BME": {
        1: [("HS3152","Professional English I","core"),("MA3151","Engineering Mathematics I","core"),
            ("PH3151","Engineering Physics","core"),("CY3151","Engineering Chemistry","core"),
            ("GE3151","Problem Solving & Python Programming","core"),("GE3152","Engineering Graphics","core"),
            ("GE3161","Python Programming Laboratory","lab"),("BS3161","Physics & Chemistry Laboratory","lab")],
        2: [("HS3252","Professional English II","core"),("MA3251","Engineering Mathematics II","core"),
            ("PH3256","Physics for Information Science","core"),("BE3251","Basic Electrical & Electronics Engg","core"),
            ("GE3251","Environmental Science & Sustainability","core"),
            ("GE3271","Engineering Practices Laboratory","lab"),("GE3272","Communication Laboratory","lab")],
        3: [("MA3354","Discrete Mathematics","core"),("BM3351","Human Anatomy & Physiology","core"),
            ("BM3352","Biomedical Instrumentation","core"),("BM3361","Human Physiology Laboratory","lab"),
            ("BM3362","Biomedical Instrumentation Laboratory","lab")],
        4: [("BM3451","Medical Electronics","core"),("BM3452","Signals & Systems for Biomedical Engineers","core"),
            ("BM3453","Biomaterials","core"),("BM3461","Medical Electronics Laboratory","lab"),
            ("BM3462","Biomedical Signal Processing Laboratory","lab")],
        5: [("BM3501","Medical Imaging Systems","core"),("BM3502","Rehabilitation Engineering","core"),
            ("BM3503","Clinical Engineering","core"),("BM3511","Medical Imaging Laboratory","lab"),
            ("BM3512","Clinical Engineering Laboratory","lab"),("PE1","Professional Elective I","elective")],
        6: [("BM3601","Biomedical Signal Processing","core"),("BM3602","Artificial Organs","core"),
            ("BM3603","Health Care Informatics","core"),("BM3611","Biomedical Signal Processing Laboratory","lab"),
            ("PE2","Professional Elective II","elective"),("OE","Open Elective","elective")],
        7: [("BM3701","Biomedical Device Design","core"),("BM3711","Project Phase I","project"),
            ("PE3","Professional Elective III","elective"),("PE4","Professional Elective IV","elective"),
            ("OE2","Open Elective II","elective")],
        8: [("BM3811","Project Work","project"),("PE5","Professional Elective V","elective")],
    },
}


# =============================================================
# FACULTY DATA — 50 faculty (FAC001-FAC050)
# =============================================================

FACULTY_DATA = [
    # fac_id, name, gender, dept, designation, email, mobile, spec, qual, dob
    # CSE dept (1-7)
    ("FAC001","Arjun Sharma",     "M","CSE", "Professor",           "arjun.sharma@college.edu",   "9876543001","CS",  "PhD","1972-04-15"),
    ("FAC002","Vikram Rajan",     "M","CSE", "Associate Professor", "vikram.rajan@college.edu",   "9876543002","CS",  "PhD","1978-08-22"),
    ("FAC003","Suresh Kumar",     "M","CSE", "Assistant Professor", "suresh.kumar@college.edu",   "9876543003","CS",  "ME", "1984-03-10"),
    ("FAC004","Ravi Shankar",     "M","CSE", "Assistant Professor", "ravi.shankar@college.edu",   "9876543004","CS",  "ME", "1986-11-05"),
    ("FAC005","Deepak Nair",      "M","CSE", "Assistant Professor", "deepak.nair@college.edu",    "9876543005","CS",  "BE", "1989-06-18"),
    ("FAC006","Anand Krishnan",   "M","CSE", "Assistant Professor", "anand.krishnan@college.edu", "9876543006","CS",  "ME", "1987-01-30"),
    ("FAC007","Karthik Murugan",  "M","CSE", "Assistant Professor", "karthik.murugan@college.edu","9876543007","CS",  "ME", "1990-09-14"),
    # AIDS dept (8-13)
    ("FAC008","Balasubramanian R","M","AIDS","Professor",           "bala.r@college.edu",         "9876543008","CS",  "PhD","1970-02-28"),
    ("FAC009","Senthil Kumar",    "M","AIDS","Associate Professor", "senthil.kumar@college.edu",  "9876543009","CS",  "PhD","1976-07-12"),
    ("FAC010","Govindarajan S",   "M","AIDS","Assistant Professor", "govind.s@college.edu",       "9876543010","CS",  "ME", "1983-05-25"),
    ("FAC011","Prakash Venkat",   "M","AIDS","Assistant Professor", "prakash.v@college.edu",      "9876543011","CS",  "ME", "1985-10-08"),
    ("FAC012","Ramesh Babu",      "M","AIDS","Assistant Professor", "ramesh.b@college.edu",       "9876543012","CS",  "BE", "1988-12-20"),
    ("FAC013","Harish Natarajan", "M","AIDS","Assistant Professor", "harish.n@college.edu",       "9876543013","CS",  "ME", "1991-03-03"),
    # CSBS dept (14-16)
    ("FAC014","Mohan Raj",        "M","CSBS","Associate Professor", "mohan.raj@college.edu",      "9876543014","CS",  "MBA","1979-09-17"),
    ("FAC015","Narayanan T",      "M","CSBS","Assistant Professor", "narayanan.t@college.edu",    "9876543015","CS",  "ME", "1984-06-06"),
    ("FAC016","Palani Selvam",    "M","CSBS","Assistant Professor", "palani.s@college.edu",       "9876543016","CS",  "ME", "1987-04-22"),
    # ECE dept (17-21)
    ("FAC017","Rajkumar P",       "M","ECE", "Professor",           "rajkumar.p@college.edu",     "9876543017","ECE", "PhD","1973-11-11"),
    ("FAC018","Sridhar Venu",     "M","ECE", "Associate Professor", "sridhar.v@college.edu",      "9876543018","ECE", "PhD","1977-08-30"),
    ("FAC019","Thirumurugan K",   "M","ECE", "Assistant Professor", "thiru.k@college.edu",        "9876543019","ECE", "ME", "1985-02-14"),
    ("FAC020","Arun Prasad",      "M","ECE", "Assistant Professor", "arun.p@college.edu",         "9876543020","ECE", "ME", "1988-07-07"),
    ("FAC021","Chandrasekaran M", "M","ECE", "Assistant Professor", "chandra.m@college.edu",      "9876543021","ECE", "BE", "1990-01-19"),
    # EEE dept (22-24)
    ("FAC022","Dinesh Babu",      "M","EEE", "Associate Professor", "dinesh.b@college.edu",       "9876543022","EEE", "PhD","1980-05-05"),
    ("FAC023","Elango R",         "M","EEE", "Assistant Professor", "elango.r@college.edu",       "9876543023","EEE", "ME", "1986-09-23"),
    ("FAC024","Ganesh Sundaram",  "M","EEE", "Assistant Professor", "ganesh.s@college.edu",       "9876543024","EEE", "ME", "1989-12-01"),
    # MECH dept (25)
    ("FAC025","Palaniappan V",    "M","MECH","Associate Professor", "palani.v@college.edu",       "9876543025","MECH","PhD","1975-03-16"),
    # Female faculty (26-50)
    ("FAC026","Priya Lakshmi",    "F","CSE", "Assistant Professor", "priya.l@college.edu",        "9876543026","CS",  "ME", "1986-08-12"),
    ("FAC027","Kavitha Devi",     "F","CSE", "Assistant Professor", "kavitha.d@college.edu",      "9876543027","CS",  "ME", "1988-04-27"),
    ("FAC028","Meena Sundari",    "F","CSE", "Assistant Professor", "meena.s@college.edu",        "9876543028","CS",  "BE", "1991-10-15"),
    ("FAC029","Revathi Nair",     "F","AIDS","Assistant Professor", "revathi.n@college.edu",      "9876543029","CS",  "ME", "1987-06-09"),
    ("FAC030","Sumathi Krishnan", "F","AIDS","Assistant Professor", "sumathi.k@college.edu",      "9876543030","CS",  "ME", "1989-02-20"),
    ("FAC031","Anitha Raman",     "F","AIDS","Assistant Professor", "anitha.r@college.edu",       "9876543031","CS",  "ME", "1990-11-04"),
    ("FAC032","Bharathi Murugan", "F","CSBS","Assistant Professor", "bharathi.m@college.edu",     "9876543032","CS",  "MBA","1985-07-18"),
    ("FAC033","Chitra Venkat",    "F","CSBS","Assistant Professor", "chitra.v@college.edu",       "9876543033","CS",  "ME", "1988-01-31"),
    ("FAC034","Divya Senthil",    "F","ECE", "Assistant Professor", "divya.s@college.edu",        "9876543034","ECE", "ME", "1989-05-22"),
    ("FAC035","Eswari Pandian",   "F","ECE", "Assistant Professor", "eswari.p@college.edu",       "9876543035","ECE", "ME", "1991-09-13"),
    ("FAC036","Geetha Balan",     "F","EEE", "Assistant Professor", "geetha.b@college.edu",       "9876543036","EEE", "ME", "1987-03-07"),
    ("FAC037","Hema Malini R",    "F","EEE", "Assistant Professor", "hema.r@college.edu",         "9876543037","EEE", "ME", "1990-08-24"),
    ("FAC038","Indira Devi",      "F","MECH","Assistant Professor", "indira.d@college.edu",       "9876543038","MECH","ME", "1986-12-16"),
    ("FAC039","Jayanthi S",       "F","MECH","Assistant Professor", "jayanthi.s@college.edu",     "9876543039","MECH","ME", "1989-04-03"),
    ("FAC040","Komala Priya",     "F","CIVIL","Associate Professor","komala.p@college.edu",       "9876543040","CIVIL","PhD","1981-06-28"),
    ("FAC041","Latha Rajan",      "F","CIVIL","Assistant Professor","latha.r@college.edu",        "9876543041","CIVIL","ME", "1984-10-10"),
    ("FAC042","Malathi Kumar",    "F","CIVIL","Assistant Professor","malathi.k@college.edu",      "9876543042","CIVIL","ME", "1988-07-01"),
    ("FAC043","Nirmala Devi",     "F","BME", "Associate Professor", "nirmala.d@college.edu",      "9876543043","BME", "PhD","1978-02-14"),
    ("FAC044","Padmavathi R",     "F","BME", "Assistant Professor", "padma.r@college.edu",        "9876543044","BME", "ME", "1985-05-19"),
    ("FAC045","Radha Krishnan",   "F","BME", "Assistant Professor", "radha.k@college.edu",        "9876543045","BME", "ME", "1988-11-26"),
    # Shared/floating faculty
    ("FAC046","Saranya Mohan",    "F","CSE", "Assistant Professor", "saranya.m@college.edu",      "9876543046","CS",  "ME", "1990-03-08"),
    ("FAC047","Tamilarasi K",     "F","AIDS","Assistant Professor", "tamilarasi.k@college.edu",   "9876543047","CS",  "ME", "1987-09-15"),
    ("FAC048","Uma Devi",         "F","ECE", "Assistant Professor", "uma.d@college.edu",          "9876543048","ECE", "ME", "1989-01-22"),
    ("FAC049","Vijayalakshmi S",  "F","MECH","Assistant Professor", "vijaya.s@college.edu",       "9876543049","MECH","ME", "1986-06-30"),
    ("FAC050","Yamuna Priya",     "F","CIVIL","Assistant Professor","yamuna.p@college.edu",       "9876543050","CIVIL","ME", "1991-08-05"),
]

# =============================================================
# PERIOD SLOTS
# =============================================================

PERIOD_SLOTS = [
    {"no": 1, "start": "09:00", "end": "10:00"},
    {"no": 2, "start": "10:00", "end": "11:00"},
    # BREAK 11:00-11:15
    {"no": 3, "start": "11:15", "end": "12:15"},
    {"no": 4, "start": "12:15", "end": "13:15"},
    # LUNCH 13:15-14:00
    {"no": 5, "start": "14:00", "end": "15:00"},
    {"no": 6, "start": "15:00", "end": "16:00"},
    {"no": 7, "start": "16:00", "end": "17:00"},
]

DAYS = ["MON", "TUE", "WED", "THU", "FRI"]

# Lab pairs (consecutive periods for 2-hr labs)
LAB_PAIRS = [(1, 2), (3, 4), (5, 6)]
THEORY_SLOTS = [1, 2, 3, 4, 5, 6, 7]


# =============================================================
# SEEDED RANDOM (deterministic based on dept+sem+section)
# =============================================================

def _seeded(dept, sem, section="A"):
    seed = sum(ord(c) for c in dept) * 1000 + sem * 100 + ord(section) * 17
    rng = random.Random(seed)
    return rng


def _faculty_for_subject(dept, sem, course_code, section="A"):
    """Deterministically assign faculty to a subject (cross-dept allowed)."""
    rng = _seeded(course_code + dept, sem, section)
    return FACULTY_DATA[rng.randint(0, 49)]


def _build_grid(dept, sem, section="A"):
    """Build a weekly timetable grid for given dept/sem/section.
    Returns list of (day, period_no, course_code, course_name, fac_id, fac_name, is_lab)
    """
    courses = COURSES.get(dept, {}).get(sem, [])
    rng = _seeded(dept, sem, section)

    labs = [c for c in courses if c[2] in ("lab", "project")]
    theory = [c for c in courses if c[2] not in ("lab", "project")]

    slots = []  # (day, period_no)
    used = set()

    # Place labs first (consecutive pairs)
    for i, lab in enumerate(labs):
        pair = LAB_PAIRS[i % len(LAB_PAIRS)]
        placed = False
        for _ in range(30):
            day = rng.choice(DAYS)
            p1, p2 = pair
            k1, k2 = f"{day}-{p1}", f"{day}-{p2}"
            if k1 not in used and k2 not in used:
                used.add(k1); used.add(k2)
                fac = _faculty_for_subject(dept, sem, lab[0], section)
                slots.append((day, p1, lab[0], lab[1], fac[0], fac[1], True))
                slots.append((day, p2, lab[0], lab[1], fac[0], fac[1], True))
                placed = True
                break
        # fallback: skip if no room

    # Place theory (each subject gets 3 slots/week)
    available = []
    for day in DAYS:
        for p in THEORY_SLOTS:
            k = f"{day}-{p}"
            if k not in used:
                available.append((day, p))

    rng.shuffle(available)
    slot_idx = 0
    for subj in theory:
        fac = _faculty_for_subject(dept, sem, subj[0], section)
        placed = 0
        while placed < 3 and slot_idx < len(available):
            day, p = available[slot_idx]
            k = f"{day}-{p}"
            if k not in used:
                used.add(k)
                slots.append((day, p, subj[0], subj[1], fac[0], fac[1], False))
                placed += 1
            slot_idx += 1

    return slots


# =============================================================
# DATABASE SETUP HELPERS
# =============================================================

def _ensure_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS faculty (
        fac_id        TEXT PRIMARY KEY,
        name          TEXT NOT NULL,
        gender        TEXT,
        dept          TEXT,
        designation   TEXT,
        email         TEXT UNIQUE,
        mobile        TEXT,
        specialization TEXT,
        qualification TEXT,
        date_of_birth TEXT DEFAULT '1990-01-01',
        password_hash TEXT DEFAULT '',
        active        INTEGER DEFAULT 1,
        created_at    TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS courses (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        dept        TEXT NOT NULL,
        year        INTEGER NOT NULL,
        semester    INTEGER NOT NULL,
        course_code TEXT NOT NULL,
        course_name TEXT NOT NULL,
        course_type TEXT DEFAULT 'core',
        credits     INTEGER DEFAULT 3,
        created_at  TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(dept, semester, course_code)
    );

    CREATE TABLE IF NOT EXISTS student_timetable (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        dept         TEXT NOT NULL,
        year         INTEGER NOT NULL,
        semester     INTEGER NOT NULL DEFAULT 1,
        section      TEXT NOT NULL DEFAULT 'A',
        day_of_week  TEXT NOT NULL,
        period_no    INTEGER NOT NULL,
        course_code  TEXT NOT NULL,
        course_name  TEXT NOT NULL,
        faculty_id   TEXT DEFAULT '',
        faculty_name TEXT DEFAULT '',
        room         TEXT DEFAULT '',
        is_lab       INTEGER DEFAULT 0,
        created_at   TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(dept, year, semester, section, day_of_week, period_no)
    );

    CREATE TABLE IF NOT EXISTS staff_timetable (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        faculty_id   TEXT NOT NULL,
        day_of_week  TEXT NOT NULL,
        period_no    INTEGER NOT NULL,
        dept         TEXT NOT NULL,
        year         INTEGER NOT NULL,
        section      TEXT NOT NULL DEFAULT 'A',
        semester     INTEGER NOT NULL DEFAULT 1,
        course_code  TEXT NOT NULL,
        course_name  TEXT NOT NULL,
        room         TEXT DEFAULT '',
        is_lab       INTEGER DEFAULT 0,
        created_at   TEXT DEFAULT (datetime('now','localtime')),
        UNIQUE(faculty_id, day_of_week, period_no, semester)
    );

    CREATE INDEX IF NOT EXISTS idx_stt_dept ON student_timetable(dept, year, semester, section);
    CREATE INDEX IF NOT EXISTS idx_ftt_fac  ON staff_timetable(faculty_id);
    CREATE INDEX IF NOT EXISTS idx_ftt_day  ON staff_timetable(day_of_week);
    """)


def _safe_add_col(conn, table, col, defn):
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
    except Exception:
        pass


# =============================================================
# SEED FUNCTIONS
# =============================================================

def seed_faculty(conn):
    """Insert all 50 faculty members."""
    inserted = 0
    for f in FACULTY_DATA:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO faculty
                    (fac_id,name,gender,dept,designation,email,mobile,specialization,qualification,dob,active)
                VALUES (?,?,?,?,?,?,?,?,?,?,1)
            """, (f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7], f[8], f[9] if len(f)>9 else ''))
            inserted += 1
        except Exception as e:
            log.warning("Faculty insert skip %s: %s", f[0], e)
    print(f"  Faculty: {inserted} seeded ({len(FACULTY_DATA)} total)")


def seed_courses(conn):
    """Insert course catalogue for all depts/sems."""
    inserted = 0
    for dept, sems in COURSES.items():
        for sem, course_list in sems.items():
            year = (sem + 1) // 2
            for code, name, ctype in course_list:
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO courses
                            (dept, year, semester, course_code, course_name, course_type, credits)
                        VALUES (?,?,?,?,?,?,?)
                    """, (dept, year, sem, code, name, ctype,
                          1 if ctype in ("lab","project") else 4 if ctype == "elective" else 3))
                    inserted += 1
                except Exception as e:
                    log.warning("Course insert skip %s/%s: %s", dept, code, e)
    print(f"  Courses: {inserted} seeded")


def seed_timetables(conn):
    """Seed student_timetable and staff_timetable for all depts/sems/sections."""
    depts = list(COURSES.keys())
    sections = ["A", "B", "C"]
    student_rows = 0
    staff_rows = 0

    for dept in depts:
        for sem in range(1, 9):
            year = (sem + 1) // 2
            for section in sections:
                grid = _build_grid(dept, sem, section)
                # generate room codes
                for (day, period_no, code, name, fac_id, fac_name, is_lab) in grid:
                    room = f"{dept[:2]}-{200 + sem*10 + (period_no%5)}"
                    if is_lab:
                        room = f"{dept[:2]}-LAB{sem}"
                    try:
                        conn.execute("""
                            INSERT OR IGNORE INTO student_timetable
                                (dept,year,semester,section,day_of_week,period_no,
                                 course_code,course_name,faculty_id,faculty_name,room,is_lab)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                        """, (dept, year, sem, section, day, period_no,
                              code, name, fac_id, fac_name, room, 1 if is_lab else 0))
                        student_rows += 1
                    except Exception:
                        pass

                    if fac_id:
                        try:
                            conn.execute("""
                                INSERT OR IGNORE INTO staff_timetable
                                    (faculty_id,day_of_week,period_no,dept,year,section,
                                     semester,course_code,course_name,room,is_lab)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                            """, (fac_id, day, period_no, dept, year, section,
                                  sem, code, name, room, 1 if is_lab else 0))
                            staff_rows += 1
                        except Exception:
                            pass

    print(f"  Student timetable slots: {student_rows}")
    print(f"  Staff timetable slots:   {staff_rows}")


def seed_all_timetables(force=False):
    """
    Main entry point. Seeds faculty, courses, and full timetables.
    If force=True, clears existing timetable data first.
    """
    print("\n=== Timetable Seed Starting ===")
    with _db() as conn:
        _ensure_tables(conn)
        _safe_add_col(conn, "student_timetable", "is_lab", "INTEGER DEFAULT 0")
        _safe_add_col(conn, "staff_timetable",   "is_lab", "INTEGER DEFAULT 0")
        _safe_add_col(conn, "student_timetable", "semester", "INTEGER NOT NULL DEFAULT 1")
        _safe_add_col(conn, "staff_timetable",   "semester", "INTEGER NOT NULL DEFAULT 1")

        if force:
            print("  Clearing existing timetable data...")
            conn.execute("DELETE FROM student_timetable")
            conn.execute("DELETE FROM staff_timetable")
            conn.execute("DELETE FROM courses")

        seed_faculty(conn)
        seed_courses(conn)
        seed_timetables(conn)

    print("=== Timetable Seed Complete ===\n")


# =============================================================
# API HELPERS — called by api_extras.py
# =============================================================

def get_all_faculty():
    with _db() as c:
        rows = c.execute(
            "SELECT * FROM faculty WHERE active=1 ORDER BY dept, name"
        ).fetchall()
        return [dict(r) for r in rows]


def get_student_timetable(dept, year, section, semester=None):
    with _db() as c:
        if semester is not None:
            rows = c.execute("""
                SELECT * FROM student_timetable
                WHERE dept=? AND year=? AND semester=? AND section=?
                ORDER BY day_of_week, period_no
            """, (dept, int(year), int(semester), section)).fetchall()
        else:
            rows = c.execute("""
                SELECT * FROM student_timetable
                WHERE dept=? AND year=? AND section=?
                ORDER BY day_of_week, period_no
            """, (dept, int(year), section)).fetchall()
        return [dict(r) for r in rows]


def get_staff_timetable(faculty_id, dept=None, semester=None):
    with _db() as c:
        sql = "SELECT * FROM staff_timetable WHERE faculty_id=?"
        params = [faculty_id]
        if dept:
            sql += " AND dept=?"
            params.append(dept)
        if semester is not None:
            sql += " AND semester=?"
            params.append(semester)
        sql += " ORDER BY day_of_week, period_no"
        rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_courses_by_dept_year(dept, year):
    sem1, sem2 = int(year)*2 - 1, int(year)*2
    with _db() as c:
        rows = c.execute("""
            SELECT * FROM courses
            WHERE dept=? AND year=?
            ORDER BY semester, course_type, course_code
        """, (dept, int(year))).fetchall()
        return [dict(r) for r in rows]


def get_period_slots():
    return PERIOD_SLOTS


# =============================================================
# STANDALONE RUN
# =============================================================
if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    seed_all_timetables(force=force)
    print("Done. Run with --force to clear and re-seed.")
