import os
from flask import (
    Flask, render_template, redirect, url_for,
    session, request, flash
)
from flask_sqlalchemy import SQLAlchemy
from flask_dance.contrib.google import make_google_blueprint, google
from werkzeug.security import generate_password_hash, check_password_hash

# ───────────── App Setup ─────────────
app = Flask(__name__)
# Set a strong secret key for session management
app.secret_key = os.getenv("SECRET_KEY", "a_very_secret_key_that_should_be_randomized_in_production")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False # Suppress warning
db = SQLAlchemy(app)

# ─────── Google OAuth Blueprint ───────
# IMPORTANT: For local development, OAUTHLIB_INSECURE_TRANSPORT is set to "1".
# In a production environment, ensure you use HTTPS and remove this line.
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
google_bp = make_google_blueprint(
    client_id="1098061362692-746k7fa0tcr9kirj0rc6t5kh64i1hq1j.apps.googleusercontent.com",
    client_secret="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",
    scope=[
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile"
    ],
    redirect_to="google_after_login"
)
app.register_blueprint(google_bp, url_prefix="/login")

# ─────────────── Models ───────────────
class User(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    email      = db.Column(db.String(120), unique=True, nullable=False)
    name       = db.Column(db.String(120))
    apartment  = db.Column(db.String(50))
    google_id  = db.Column(db.String(50))
    password   = db.Column(db.String(128)) # Hashed password
    is_admin   = db.Column(db.Boolean, default=False)
    picture    = db.Column(db.String(255), nullable=True) # Added picture column

    def __repr__(self):
        return f"<User {self.email}>"

class Tenant(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    name      = db.Column(db.String(120), nullable=False)
    phone     = db.Column(db.String(50))
    apartment = db.Column(db.String(50))

    def __repr__(self):
        return f"<Tenant {self.name} - Apt {self.apartment}>"

# Global list for reminders (Note: This is not persistent across server restarts
# and is shared by all users. For a production app, this should be stored in a database
# and associated with specific users or the building.)
reminders = []

# ─────────────── Routes ───────────────

@app.route("/")
def welcome():
    # Render the welcome page directly as the entry point
    return render_template("welcome.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        name = request.form["name"]
        apartment = request.form["apartment"]
        password = request.form["password"]

        # Check if user already exists
        if User.query.filter_by(email=email).first():
            flash("An account with this email already exists. Please log in.", "warning")
            return redirect(url_for("manual_login"))

        # Create new user with hashed password
        new_user = User(
            email=email,
            name=name,
            apartment=apartment,
            password=generate_password_hash(password)
        )
        db.session.add(new_user)
        db.session.commit()
        flash("Account created successfully! Please log in.", "success")
        return redirect(url_for("manual_login"))
    return render_template("signup.html")


@app.route("/manual-login", methods=["GET", "POST"])
def manual_login():
    if "uid" in session: # If already logged in, redirect to dashboard
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        user = User.query.filter_by(email=email).first()
        # Check if user exists and password is correct
        if user and check_password_hash(user.password, password):
            # Store user details in session
            session["uid"] = user.id
            session["username"] = user.name
            session["apartment"] = user.apartment
            session["is_admin"] = user.is_admin
            session["picture"] = user.picture # This will now work as 'picture' column exists
            print(f"User {user.email} logged in. Is Admin: {user.is_admin}") # Debug print
            flash(f"Welcome, {user.name}!", "info")
            return redirect(url_for("dashboard"))
        flash("Invalid email or password. Please try again.", "danger")
    return render_template("login_manual.html")

@app.route("/google/callback")
def google_after_login():
    if not google.authorized:
        flash("Google login failed. Please try again.", "danger")
        return redirect(url_for("manual_login"))

    try:
        resp = google.get("/oauth2/v2/userinfo")
        resp.raise_for_status() # Raise an exception for HTTP errors
        data = resp.json()
    except Exception as e:
        flash(f"Failed to fetch Google user info: {e}", "danger")
        return redirect(url_for("manual_login"))

    email = data["email"]

    user = User.query.filter_by(email=email).first()
    if not user:
        # Create new user if not found
        user = User(email=email)
        db.session.add(user)
        flash("New account created via Google!", "success")
    else:
        flash(f"Welcome back, {user.name}!", "info")

    # Update user details from Google
    user.name = data.get("name")
    user.google_id = data.get("id")
    user.picture = data.get("picture")
    # Note: Apartment and password are not set via Google OAuth,
    # they would need to be updated by the user manually or during initial signup.
    db.session.commit()

    # Store user details in session
    session["uid"] = user.id
    session["username"] = user.name
    session["apartment"] = user.apartment
    session["is_admin"] = user.is_admin
    session["picture"] = data.get("picture") # Store Google profile picture URL
    print(f"User {user.email} logged in via Google. Is Admin: {user.is_admin}") # Debug print

    return redirect(url_for("dashboard"))

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    # Ensure user is logged in
    if "uid" not in session:
        flash("Please log in to access the dashboard.", "info")
        return redirect(url_for("manual_login"))

    result = None
    # Handle POST requests for maintenance calculation or adding reminders
    if request.method == "POST":
        if "electricity" in request.form and "water" in request.form and "watchman" in request.form and "flats" in request.form:
            try:
                e  = float(request.form["electricity"])
                w  = float(request.form["water"])
                wt = float(request.form["watchman"])
                f  = int(request.form["flats"])

                if f <= 0:
                    flash("Number of flats must be greater than zero.", "warning")
                else:
                    total = e + w + wt
                    per_flat = total / f
                    result = dict(total=total, per_flat=per_flat)
                    flash("Maintenance calculated successfully!", "success")
            except ValueError:
                flash("Please enter valid numbers for all fields.", "danger")
        elif "reminder" in request.form:
            reminder_text = request.form["reminder"].strip()
            if reminder_text:
                reminders.append(reminder_text)
                flash("Reminder added successfully!", "success")
            else:
                flash("Reminder cannot be empty.", "warning")

    # Render dashboard with current session info, calculation result, and reminders
    return render_template(
        "dashboard.html",
        user=session.get("username"),
        apartment=session.get("apartment"),
        picture=session.get("picture"),
        is_admin=session.get("is_admin"),
        result=result,
        reminders=reminders
    )

@app.route("/tenants", methods=["GET", "POST"])
def view_tenants():
    # Ensure user is logged in
    if "uid" not in session:
        flash("Please log in to view tenant information.", "info")
        return redirect(url_for("manual_login"))

    is_admin = session.get("is_admin")
    # Handle POST request for adding a new tenant (admin only)
    if request.method == "POST":
        if not is_admin:
            flash("You do not have permission to add tenants.", "danger")
            return redirect(url_for("view_tenants")) # Redirect instead of Forbidden
        
        name  = request.form["name"].strip()
        phone = request.form["phone"].strip()
        apt   = request.form["apartment"].strip()

        if not (name and phone and apt):
            flash("All tenant fields are required.", "warning")
            return redirect(url_for("view_tenants"))

        # Check for duplicate apartment number for simplicity (can be refined)
        if Tenant.query.filter_by(apartment=apt).first():
            flash(f"Apartment {apt} already has a tenant listed.", "warning")
            return redirect(url_for("view_tenants"))

        t = Tenant(name=name, phone=phone, apartment=apt)
        db.session.add(t)
        db.session.commit()
        flash(f"Tenant {name} added for Apartment {apt}.", "success")
        return redirect(url_for("view_tenants"))

    # Fetch all tenants for display, ordered by apartment number
    all_tenants = Tenant.query.order_by(Tenant.apartment).all()
    return render_template("tenants.html", tenants=all_tenants, is_admin=is_admin)

@app.route("/tenants/delete/<int:tid>")
def delete_tenant(tid):
    # Ensure only admin can delete tenants
    if not session.get("is_admin"):
        flash("You do not have permission to delete tenants.", "danger")
        return redirect(url_for("view_tenants"))
    
    tenant_to_delete = Tenant.query.get(tid)
    if tenant_to_delete:
        db.session.delete(tenant_to_delete)
        db.session.commit()
        flash(f"Tenant {tenant_to_delete.name} deleted successfully.", "success")
    else:
        flash("Tenant not found.", "warning")
    return redirect(url_for("view_tenants"))

@app.route("/logout")
def logout():
    session.clear() # Clear all session data
    flash("You have been logged out.", "info")
    return redirect(url_for("manual_login"))

@app.route("/forgot-password")
def forgot_password():
    # This is a placeholder route. In a real application, you would implement
    # functionality to send a password reset email here.
    flash("Password reset functionality is not yet implemented.", "info")
    return redirect(url_for("manual_login"))


# ───────────── Seed DB & Admin ─────────────
if __name__ == "__main__":
    with app.app_context():
        db.create_all() # Create database tables if they don't exist

        # Create a default admin user if one doesn't exist
        if not User.query.filter_by(is_admin=True).first():
            admin = User(
                email="admin@tranquil.local",
                name="Admin User",
                is_admin=True,
                password=generate_password_hash("Admin@123") # Strong password for admin
            )
            db.session.add(admin)
            db.session.commit()
            print("Default admin user created: admin@tranquil.local with password 'Admin@123'")
        
        # Add some sample tenants if none exist
        if not Tenant.query.first():
            sample_tenants = [
                Tenant(name="John Doe", phone="111-222-3333", apartment="A101"),
                Tenant(name="Jane Smith", phone="444-555-6666", apartment="B202"),
                Tenant(name="Peter Jones", phone="777-888-9999", apartment="C303"),
            ]
            db.session.add_all(sample_tenants)
            db.session.commit()
            print("Sample tenants added to the database.")

    app.run(debug=True) # Run the Flask application in debug mode
