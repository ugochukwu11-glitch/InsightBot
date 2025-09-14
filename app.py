from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, UserMixin, current_user
from flask_bcrypt import Bcrypt
import mysql.connector

app = Flask(__name__)
app.secret_key = "supersecret"

# ---- MySQL Config ----
db_config = {
    "host": "localhost",
    "user": "root",
    "password": "",
    "database": "insightbot"
}

bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# ---- User Class ----
class User(UserMixin):
    def __init__(self, id, username, is_admin, is_approved):
        self.id = id
        self.username = username
        self.is_admin = is_admin
        self.is_approved = is_approved

@login_manager.user_loader
def load_user(user_id):
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()
    conn.close()
    if user:
        return User(user["id"], user["username"], user["is_admin"], user["is_approved"])
    return None

# ---- Routes ----

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        email = request.form.get("email")

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            conn.close()
            flash("Username already exists")
            return redirect(url_for("register"))

        pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        cursor.execute("INSERT INTO users (username, password_hash, email) VALUES (%s, %s, %s)",
                       (username, pw_hash, email))
        conn.commit()
        conn.close()
        flash("Registered successfully. Wait for admin approval.")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cursor.fetchone()
        conn.close()

        if user and bcrypt.check_password_hash(user["password_hash"], password):
            if not user["is_approved"]:
                flash("Your account is pending admin approval.")
                return redirect(url_for("login"))
            login_user(User(user["id"], user["username"], user["is_admin"], user["is_approved"]))
            return redirect(url_for("index"))
        else:
            flash("Invalid credentials")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/admin/approve")
@login_required
def admin_approve():
    if not current_user.is_admin:
        return "Access denied", 403
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE is_approved = FALSE")
    users = cursor.fetchall()
    conn.close()
    return render_template("approve.html", users=users)

@app.route("/admin/approve/<int:user_id>")
@login_required
def approve_user(user_id):
    if not current_user.is_admin:
        return "Access denied", 403
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_approved = TRUE WHERE id = %s", (user_id,))
    conn.commit()
    conn.close()
    flash("User approved successfully!")
    return redirect(url_for("admin_approve"))

@app.route("/", methods=["GET"])
@login_required
def index():
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM articles ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return "No articles available yet."

    selected_lang = request.args.get("lang")
    if selected_lang:
        filtered = [row for row in rows if row["language"] == selected_lang]
    else:
        filtered = rows

    languages = list({row["language"] for row in rows if row["language"]})
    return render_template("index.html",
                           articles=filtered,
                           languages=languages,
                           selected_lang=selected_lang)

@app.route("/article/<int:article_id>")
@login_required
def article(article_id):
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM articles WHERE id = %s", (article_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return "Article not found", 404

    selected_lang = request.args.get("lang")
    return render_template("article.html", article=row, selected_lang=selected_lang)

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html")

@app.route('/api/articles')
def api_articles():
    conn = mysql.connector.connect(
        host="localhost", user="root", password="", database="insightbot"
    )
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id, url, title, body, published, length, source, language
        FROM articles
    """)
    rows = cursor.fetchall()
    conn.close()
    return jsonify(rows)


@app.route("/tableau_wdc")
def tableau_wdc():
    return render_template("tableau_wdc.html")

if __name__ == "__main__":
    app.run(debug=True)
