from flask_bcrypt import Bcrypt
bcrypt = Bcrypt()
print(bcrypt.generate_password_hash("mypassword").decode("utf-8"))
