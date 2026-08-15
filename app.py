from flask import Flask, render_template, redirect, url_for
from routes.tools import tools_bp
from tools.gstr2bmerger import gstr2bmerger_bp

app = Flask(__name__)

app.secret_key = "this is our super secret key taxdecipher ####~!@"

app.register_blueprint(tools_bp)
app.register_blueprint(gstr2bmerger_bp)

@app.errorhandler(404)
def page_not_found(e):
    return render_template("error.html")

@app.route('/')
def home():
    return redirect(url_for('tools.tools'))

if __name__ == "__main__":
    app.run(debug=True)