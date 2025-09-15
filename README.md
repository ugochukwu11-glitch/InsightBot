# 📰 InsightBot  

InsightBot is a Flask-based web application that scrapes news articles from multiple global sources, stores them in MySQL, and presents refined insights through an embedded Tableau dashboard.  

## ✨ Features  
- 🔐 **User Authentication** – Registration, login, and admin approval system  
- 🌍 **Automated News Scraping** – Collects fresh news daily from major sites  
- 🗄️ **Database Storage** – Articles saved in MySQL with deduplication  
- 🧠 **Language Detection** – Detects and stores the language of each article  
- 📊 **Dashboard** – Interactive Tableau visualization embedded in the app  
- ⏰ **Scheduled Scraping** – Automatically scrapes news at set intervals  

---

## ⚙️ Installation  

1. **Clone the repo**  
   ```bash
   git clone https://github.com/ugochukwu11-glitch/insightbot.git
   cd insightbot

2. **Create a virtual environment**  
   ```bash
   python -m venv .venv
   source .venv/bin/activate      # macOS/Linux
   .venv\Scripts\activate         # Windows

3. **Install dependencies**  
   ```bash
   pip install -r requirements.txt

4. **Configure MySQL**  

   Ensure MySQL is installed and running
Create a database named insightbot
Update DB_CONFIG in insightbot_hybrid_scraper.py with your credentials
   ```bash
   CREATE DATABASE insightbot;
---   

## 🚀 Usage
1. **Run the Flask app**
   ```bash
   python app.py
2. **Visit: http://127.0.0.1:5000/**

3. **Run the scraper manually**
   ```bash
   python insightbot_hybrid_scraper.py --mode train --per-site 3
4. **Run scheduled scraping**
   
   The scraper has a built-in scheduler (via APScheduler). It will fetch news automatically in the background once you start app.py.
---

## 📂 Project Structure
 
**insightbot/**
```bash
│── app.py                 # Flask app  
│── scraper.py             # News scraper (scheduled + manual modes)  
│── requirements.txt       # Dependencies  
│── templates/             # HTML templates (dashboard, login, register, etc.)  
│── static/                # CSS, JS, and static files  
│── scraper.log            # Log file for scraper activity  
│── news_hybrid_training.* # Auto-generated scraped data  
│── news_hybrid_testing.*  # Auto-generated scraped data 
```
---
## 📊 Dashboard
**InsightBot integrates an interactive Tableau dashboard via dashboard.html to visualize trends in the scraped articles.**

---

## 🛠️ Tech Stack
-  **Backend:** Python, Flask
- **Scraping:** BeautifulSoup, Requests
- **Database:** MySQL
- **Scheduler:** APScheduler
- **Visualization:** Tableau (embedded)


