from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from datetime import datetime
from functools import wraps
import os, json, random, string, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from PIL import Image
import pandas as pd
from io import BytesIO
import io
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from models import db, User, Product, Category, Order, OrderItem, Sale, SaleItem, Debt, StoreConfig

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'default_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URI', 'sqlite:///catashop.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

db.init_app(app)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["1000 per day", "100 per hour"]
)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

UPLOAD_FOLDER = os.path.join('static', 'images', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'jfif'}

app.config['WTF_CSRF_ENABLED'] = False

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def optimize_image(file, max_size=(800, 800)):
    try:
        img = Image.open(file)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        output = BytesIO()
        img.save(output, format='WEBP', quality=85)
        output.seek(0)
        return output
    except Exception as e:
        print(f"Error optimizando imagen: {e}")
        return file

def generate_order_number():
    return 'CS-' + ''.join(random.choices(string.digits, k=6))

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# Template filter for COP currency
@app.template_filter('cop')
def format_cop(value):
    try:
        return "${:,.0f}".format(float(value)).replace(",", ".")
    except (ValueError, TypeError):
        return "$0"

@app.template_filter('from_json')
def from_json(value):
    import json
    try:
        return json.loads(value) if value else {}
    except Exception:
        return {}

# Cart count context processor
@app.context_processor
def inject_global_data():
    cart = session.get('cart', {})
    count = sum(item.get('quantity', 1) for item in cart.values()) if cart else 0
    unread = 0
    if current_user.is_authenticated:
        unread = Order.query.filter_by(user_id=current_user.id, is_message_read=False).filter(Order.admin_message.isnot(None)).count()
    
    low_stock = []
    if current_user.is_authenticated and getattr(current_user, 'role', '') == 'admin':
        for p in Product.query.all():
            if p.sizes:
                try:
                    s_dict = json.loads(p.sizes)
                    if any(int(qty) < 3 for qty in s_dict.values()):
                        low_stock.append(p)
                except: pass
    pending_orders = 0
    if current_user.is_authenticated and getattr(current_user, 'role', '') == 'admin':
        pending_orders = Order.query.filter_by(status='pending').count()

    return dict(cart_count=count, unread_notifications=unread, low_stock_products=low_stock, pending_orders_count=pending_orders)

@app.before_request
def restrict_admin_access():
    if current_user.is_authenticated and current_user.role == 'admin':
        allowed_prefixes = ['/admin', '/logout', '/static']
        if not any(request.path.startswith(p) for p in allowed_prefixes):
            return redirect(url_for('admin_caja'))

# ============ EMAIL FUNCTIONS ============
def send_verification_email(email, code):
    try:
        sender = 'catashoope@gmail.com'
        password = os.getenv('EMAIL_PASSWORD', '')
        if not password:
            return False
        msg = MIMEText(f"Tu codigo de verificacion para Cata Shoope es: {code}")
        msg['Subject'] = 'Verifica tu cuenta en Cata Shoope'
        msg['From'] = sender
        msg['To'] = email
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender, password)
            server.sendmail(sender, email, msg.as_string())
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

def send_invoice_email(order, user_email):
    try:
        sender = 'catashoope@gmail.com'
        password = os.getenv('EMAIL_PASSWORD', '')
        if not password:
            return False
        items_html = ''
        for item in order.items:
            product = Product.query.get(item.product_id)
            pname = product.name if product else 'Producto'
            items_html += f'''
                <tr>
                    <td style="padding:8px;border-bottom:1px solid #eee;">{pname}</td>
                    <td style="padding:8px;border-bottom:1px solid #eee;">{item.size or '-'}</td>
                    <td style="padding:8px;border-bottom:1px solid #eee;">{item.quantity}</td>
                    <td style="padding:8px;border-bottom:1px solid #eee;">${item.price:,.0f}</td>
                </tr>'''
        html_body = f'''
        <div style="max-width:600px;margin:0 auto;font-family:Arial,sans-serif;background:#fff;border-radius:12px;overflow:hidden;">
            <div style="background:linear-gradient(135deg,#E91E7A,#FF6B35);padding:30px;text-align:center;color:#fff;">
                <h1 style="margin:0;font-size:1.5rem;">Cata Shoope</h1>
                <p style="margin:8px 0 0;">Factura de Pedido</p>
            </div>
            <div style="padding:24px;">
                <p>Pedido: <strong>{order.order_number if hasattr(order, 'order_number') else f'#{order.id}'}</strong></p>
                <table style="width:100%;border-collapse:collapse;margin:16px 0;">
                    <tr style="background:#f5f5f5;">
                        <th style="padding:8px;text-align:left;">Producto</th>
                        <th style="padding:8px;text-align:left;">Talla</th>
                        <th style="padding:8px;text-align:left;">Cant.</th>
                        <th style="padding:8px;text-align:left;">Precio</th>
                    </tr>
                    {items_html}
                </table>
                <p style="text-align:right;font-size:1.2rem;font-weight:bold;">Total: ${order.total:,.0f}</p>
            </div>
            <div style="background:#f5f5f5;padding:16px;text-align:center;">
                <p style="margin:0;font-size:0.8rem;color:#999;">Gracias por tu compra en Cata Shoope</p>
            </div>
        </div>'''
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'Factura de tu pedido - Cata Shoope'
        msg['From'] = sender
        msg['To'] = user_email
        msg.attach(MIMEText(html_body, 'html'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender, password)
            server.sendmail(sender, user_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Error sending invoice: {e}")
        return False

def send_welcome_email(user_email, username):
    try:
        sender = 'catashoope@gmail.com'
        password = os.getenv('EMAIL_PASSWORD', '')
        if not password:
            return False
            
        html_body = f'''
        <div style="max-width:600px;margin:0 auto;font-family:Arial,sans-serif;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #eee;">
            <div style="background:linear-gradient(135deg,#E91E7A,#FF6B35);padding:40px 30px;text-align:center;color:#fff;">
                <h1 style="margin:0;font-size:2rem;letter-spacing:-1px;">¡Bienvenid@, {username}!</h1>
            </div>
            <div style="padding:32px 24px;color:#333;line-height:1.6;">
                <p style="font-size:1.1rem;margin-bottom:16px;">Nos emociona tenerte en <strong>Cata Shoope</strong>.</p>
                <p>Tu cuenta ha sido creada exitosamente. Ya puedes explorar nuestro catálogo, guardar tus prendas favoritas en el carrito y realizar pedidos.</p>
                <div style="text-align:center;margin:32px 0;">
                    <a href="http://127.0.0.1:5001/catalogo" style="display:inline-block;background:#1a1a1a;color:#fff;text-decoration:none;padding:14px 32px;border-radius:30px;font-weight:bold;">Ir al Catálogo</a>
                </div>
            </div>
            <div style="background:#f9f9f9;padding:20px;text-align:center;border-top:1px solid #eee;">
                <p style="margin:0;font-size:0.85rem;color:#888;">&copy; 2026 Cata Shoope. Todos los derechos reservados.</p>
            </div>
        </div>
        '''
        msg = MIMEMultipart('alternative')
        msg['Subject'] = '¡Bienvenido a Cata Shoope!'
        msg['From'] = sender
        msg['To'] = user_email
        msg.attach(MIMEText(html_body, 'html'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender, password)
            server.sendmail(sender, user_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Error sending welcome email: {e}")
        return False

def send_otp_email(user_email, otp_code):
    try:
        sender = 'catashoope@gmail.com'
        password = os.getenv('EMAIL_PASSWORD', '')
        if not password:
            return False
            
        html_body = f'''
        <div style="max-width:600px;margin:0 auto;font-family:Arial,sans-serif;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #eee;">
            <div style="background:linear-gradient(135deg,#E91E7A,#FF6B35);padding:30px;text-align:center;color:#fff;">
                <h1 style="margin:0;font-size:1.5rem;">Cata Shoope</h1>
                <p style="margin:8px 0 0;">Verifica tu correo</p>
            </div>
            <div style="padding:32px 24px;color:#333;text-align:center;">
                <p style="font-size:1.1rem;margin-bottom:16px;">Usa el siguiente código de 6 dígitos para verificar tu cuenta:</p>
                <div style="background:#f5f5f5;padding:20px;border-radius:8px;font-size:2rem;font-weight:bold;letter-spacing:4px;color:#e91e7a;display:inline-block;">{otp_code}</div>
                <p style="margin-top:24px;font-size:0.9rem;color:#666;">Si no creaste esta cuenta, ignora este correo.</p>
            </div>
        </div>
        '''
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'{otp_code} - Código de verificación Cata Shoope'
        msg['From'] = sender
        msg['To'] = user_email
        msg.attach(MIMEText(html_body, 'html'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender, password)
            server.sendmail(sender, user_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Error sending OTP: {e}")
        return False

def send_password_reset_email(user_email, reset_token):
    try:
        sender = 'catashoope@gmail.com'
        password = os.getenv('EMAIL_PASSWORD', '')
        if not password:
            return False
            
        reset_url = url_for('restablecer_password', token=reset_token, _external=True)
        html_body = f'''
        <div style="max-width:600px;margin:0 auto;font-family:Arial,sans-serif;background:#fff;border-radius:12px;overflow:hidden;border:1px solid #eee;">
            <div style="background:linear-gradient(135deg,#E91E7A,#FF6B35);padding:30px;text-align:center;color:#fff;">
                <h1 style="margin:0;font-size:1.5rem;">Cata Shoope</h1>
                <p style="margin:8px 0 0;">Recuperación de contraseña</p>
            </div>
            <div style="padding:32px 24px;color:#333;text-align:center;">
                <p style="font-size:1.1rem;margin-bottom:24px;">Hemos recibido una solicitud para cambiar tu contraseña. Haz clic en el botón de abajo para continuar:</p>
                <a href="{reset_url}" style="display:inline-block;background:#1a1a1a;color:#fff;text-decoration:none;padding:14px 32px;border-radius:30px;font-weight:bold;">Cambiar Contraseña</a>
                <p style="margin-top:24px;font-size:0.9rem;color:#666;">Si no solicitaste este cambio, ignora este correo.</p>
            </div>
        </div>
        '''
        msg = MIMEMultipart('alternative')
        msg['Subject'] = 'Recupera tu contraseña - Cata Shoope'
        msg['From'] = sender
        msg['To'] = user_email
        msg.attach(MIMEText(html_body, 'html'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender, password)
            server.sendmail(sender, user_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Error sending reset password: {e}")
        return False

# ============ PUBLIC ROUTES ============
@app.route('/')
def index():
    products = Product.query.limit(8).all()
    config = StoreConfig.query.first()
    return render_template('index.html', products=products, config=config)

@app.route('/catalogo')
@login_required
def catalogo():
    gender = request.args.get('gender')
    category_id = request.args.get('category')
    min_price = request.args.get('min_price')
    max_price = request.args.get('max_price')
    search = request.args.get('search', '')

    query = Product.query
    if gender:
        query = query.join(Category).filter(db.or_(Category.gender == gender, Category.gender.ilike('%unisex%')))
    if category_id:
        query = query.filter_by(category_id=int(category_id))
    if min_price:
        query = query.filter(Product.price >= float(min_price))
    if max_price:
        query = query.filter(Product.price <= float(max_price))
    if search:
        query = query.filter(Product.name.ilike(f'%{search}%'))

    query = query.order_by(Product.name.asc())
    products = query.all()
    categories = Category.query.order_by(Category.name.asc()).all()
    return render_template('catalogo.html', products=products, categories=categories, current_gender=gender)

@app.route('/producto/<int:pid>')
@login_required
def producto(pid):
    product = Product.query.get_or_404(pid)
    related = Product.query.filter(Product.category_id == product.category_id, Product.id != product.id).limit(4).all()
    sizes = {}
    if product.sizes:
        try:
            sizes = json.loads(product.sizes)
        except:
            sizes = {s.strip(): 10 for s in product.sizes.split(',') if s.strip()}
    return render_template('producto.html', product=product, related=related, sizes=sizes)

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute", methods=['POST'])
def login():
    if request.method == 'POST':
        action = request.form.get('action', 'login')
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        if action == 'register':
            username = request.form.get('username', '').strip()
            password_confirm = request.form.get('password_confirm', '')
            
            if password != password_confirm:
                flash('Las contraseñas no coinciden', 'error')
                return redirect(url_for('login'))
                
            if User.query.filter_by(email=email).first():
                flash('Este email ya esta registrado', 'error')
                return redirect(url_for('login'))
                
            otp = str(random.randint(100000, 999999))
            hashed = generate_password_hash(password)
            new_user = User(name=username, email=email, password_hash=hashed, is_verified=False, verification_code=otp)
            db.session.add(new_user)
            db.session.commit()
            
            send_otp_email(email, otp)
            session['verify_email'] = email
            flash('Te hemos enviado un código de verificación a tu correo.', 'info')
            return redirect(url_for('verificar_otp'))
            
        else:
            user = User.query.filter_by(email=email).first()
            if user and check_password_hash(user.password_hash, password):
                if not getattr(user, 'is_verified', True) and user.id > 1:
                    otp = str(random.randint(100000, 999999))
                    user.verification_code = otp
                    db.session.commit()
                    send_otp_email(email, otp)
                    session['verify_email'] = email
                    flash('Por favor verifica tu cuenta. Te enviamos un código al correo.', 'info')
                    return redirect(url_for('verificar_otp'))
                    
                login_user(user)
                if user.role == 'admin':
                    return redirect(url_for('admin_caja'))
                return redirect(url_for('catalogo'))
            flash('Credenciales invalidas', 'error')
    return render_template('login.html')

@app.route('/verificar_otp', methods=['GET', 'POST'])
def verificar_otp():
    email = session.get('verify_email')
    if not email:
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        otp = request.form.get('otp', '').strip()
        user = User.query.filter_by(email=email).first()
        if user and user.verification_code == otp:
            user.is_verified = True
            user.verification_code = None
            db.session.commit()
            login_user(user)
            send_welcome_email(email, user.name)
            flash('Cuenta verificada y creada exitosamente.', 'success')
            session.pop('verify_email', None)
            return redirect(url_for('catalogo'))
        else:
            flash('Código de verificación incorrecto', 'error')
            
    return render_template('verificar_otp.html', email=email)

@app.route('/recuperar_password', methods=['GET', 'POST'])
def recuperar_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        user = User.query.filter_by(email=email).first()
        if user:
            token = ''.join(random.choices(string.ascii_letters + string.digits, k=32))
            user.reset_token = token
            db.session.commit()
            send_password_reset_email(user.email, token)
        flash('Si el correo existe en nuestro sistema, te enviaremos un enlace de recuperación.', 'info')
        return redirect(url_for('login'))
    return render_template('recuperar_password.html')

@app.route('/restablecer_password/<token>', methods=['GET', 'POST'])
def restablecer_password(token):
    user = User.query.filter_by(reset_token=token).first()
    if not user:
        flash('El enlace de recuperación es inválido o ha expirado.', 'error')
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        password = request.form.get('password')
        password_confirm = request.form.get('password_confirm')
        if password != password_confirm:
            flash('Las contraseñas no coinciden', 'error')
            return redirect(url_for('restablecer_password', token=token))
            
        user.password_hash = generate_password_hash(password)
        user.reset_token = None
        db.session.commit()
        flash('Tu contraseña ha sido actualizada exitosamente.', 'success')
        return redirect(url_for('login'))
        
    return render_template('restablecer_password.html', token=token)

@app.route('/perfil', methods=['GET', 'POST'])
@login_required
def perfil():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        if username:
            current_user.name = username
            db.session.commit()
            flash('Perfil actualizado exitosamente', 'success')
            return redirect(url_for('perfil'))
    return render_template('perfil.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.pop('cart', None)
    return redirect(url_for('index'))

@app.route('/verify')
def verify():
    return render_template('verify.html')

@app.route('/carrito')
@login_required
def carrito():
    cart = session.get('cart', {})
    products = []
    total = 0
    for key, item in cart.items():
        product = Product.query.get(item.get('product_id', int(key.split('_')[0])))
        if product:
            subtotal = product.price * item.get('quantity', 1)
            total += subtotal
            products.append({
                'key': key,
                'product': product,
                'size': item.get('size', ''),
                'quantity': item.get('quantity', 1),
                'subtotal': subtotal
            })
            
    notifications = Order.query.filter_by(user_id=current_user.id).filter(Order.admin_message.isnot(None), Order.admin_message != '').order_by(Order.id.desc()).all()
    
    return render_template('carrito.html', products=products, total=total, notifications=notifications)

@app.route('/api/notifications/mark_read', methods=['POST'])
@login_required
def mark_notifications_read():
    orders = Order.query.filter_by(user_id=current_user.id, is_message_read=False).filter(Order.admin_message.isnot(None)).all()
    for o in orders:
        o.is_message_read = True
    db.session.commit()
    return jsonify({'success': True})

@app.route('/agregar_carrito/<int:pid>', methods=['POST'])
@login_required
def agregar_carrito(pid):
    size = request.form.get('size', '')
    quantity = int(request.form.get('quantity', 1))
    buy_now = request.form.get('buy_now') == 'true'
    
    if buy_now:
        return redirect(url_for('checkout', buy_now_pid=pid, size=size, qty=quantity))
        
    cart = session.get('cart', {})
    key = f"{pid}_{size}"
    if key in cart:
        cart[key]['quantity'] += quantity
    else:
        cart[key] = {'product_id': pid, 'size': size, 'quantity': quantity}
    session['cart'] = cart
    
    # If it's an AJAX request
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        count = sum(item.get('quantity', 1) for item in cart.values())
        return jsonify({'success': True, 'cart_count': count, 'message': 'Producto agregado'})
        
    flash('Producto agregado al carrito', 'success')
    return redirect(url_for('carrito'))

@app.route('/eliminar_carrito/<key>')
@login_required
def eliminar_carrito(key):
    cart = session.get('cart', {})
    cart.pop(key, None)
    session['cart'] = cart
    flash('Producto eliminado del carrito', 'success')
    return redirect(url_for('carrito'))

@app.route('/checkout', methods=['GET', 'POST'])
@login_required
def checkout():
    cart = session.get('cart', {})
    
    # Handle single item buy_now
    buy_now_pid = request.args.get('buy_now_pid') or (request.form.get('buy_now_pid') if request.method == 'POST' else None)
    buy_now_size = request.args.get('size') or (request.form.get('buy_now_size') if request.method == 'POST' else '')
    buy_now_qty = int(request.args.get('qty', 1) if request.method == 'GET' else request.form.get('buy_now_qty', 1))
    
    # Handle partial cart checkout
    selected_items_str = request.form.get('selected_items') or request.args.get('items')
    selected_items = [k for k in selected_items_str.split(',') if k] if selected_items_str else []
    
    # Determine the items to checkout
    items_to_checkout = {}
    is_buy_now = False
    
    if buy_now_pid:
        is_buy_now = True
        items_to_checkout[f"{buy_now_pid}_{buy_now_size}"] = {'product_id': int(buy_now_pid), 'size': buy_now_size, 'quantity': buy_now_qty}
    elif selected_items:
        for key in selected_items:
            if key in cart:
                items_to_checkout[key] = cart[key]
    else:
        items_to_checkout = cart.copy()

    if request.method == 'POST':
        if not items_to_checkout:
            flash('No hay productos para comprar', 'error')
            return redirect(url_for('carrito'))

        order = Order(
            user_id=current_user.id,
            total=0,
            status='pending',
            client_phone=request.form.get('phone', ''),
            client_email=request.form.get('email', current_user.email),
            client_address=request.form.get('address', ''),
            delivery_type=request.form.get('delivery_type', 'tienda'),
            payment_preference=request.form.get('payment', '')
        )
        db.session.add(order)
        db.session.flush()

        total = 0
        for key, item in items_to_checkout.items():
            product = Product.query.get(item.get('product_id', int(key.split('_')[0])))
            if product:
                subtotal = product.price * item.get('quantity', 1)
                total += subtotal
                oi = OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    quantity=item.get('quantity', 1),
                    size=item.get('size', ''),
                    price=product.price
                )
                db.session.add(oi)
        order.total = total
        db.session.commit()
        
        # Clear checked out items from session cart only if it's not a buy_now
        if not is_buy_now:
            for key in items_to_checkout.keys():
                cart.pop(key, None)
            session['cart'] = cart

        # Try to send invoice email
        send_invoice_email(order, order.client_email or current_user.email)

        flash('Pedido realizado exitosamente', 'success')
        return redirect(url_for('mis_pedidos'))

    # GET - show checkout page
    products = []
    total = 0
    for key, item in items_to_checkout.items():
        product = Product.query.get(item.get('product_id', int(key.split('_')[0])))
        if product:
            subtotal = product.price * item.get('quantity', 1)
            total += subtotal
            products.append({'product': product, 'size': item.get('size', ''), 'quantity': item.get('quantity', 1), 'subtotal': subtotal})
            
    sel_items_csv = ','.join(items_to_checkout.keys()) if not is_buy_now else ''
    return render_template('checkout.html', products=products, total=total, buy_now_pid=buy_now_pid, buy_now_size=buy_now_size, buy_now_qty=buy_now_qty, selected_items=sel_items_csv)

@app.route('/mis_pedidos/cancelar/<int:oid>', methods=['POST'])
@login_required
def cancelar_pedido(oid):
    order = Order.query.get_or_404(oid)
    if order.user_id != current_user.id:
        flash('No autorizado', 'error')
        return redirect(url_for('mis_pedidos'))
        
    if order.status == 'pending':
        order.status = 'cancelled'
        order.admin_message = 'Cancelado por el cliente.'
        db.session.commit()
        flash('Pedido cancelado exitosamente.', 'success')
    else:
        flash('Solo se pueden cancelar pedidos pendientes.', 'error')
        
    return redirect(url_for('mis_pedidos'))

@app.route('/mis_pedidos')
@login_required
def mis_pedidos():
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
    # Marcar notificaciones como leídas
    unread_orders = Order.query.filter_by(user_id=current_user.id, is_message_read=False).filter(Order.admin_message.isnot(None)).all()
    if unread_orders:
        for order in unread_orders:
            order.is_message_read = True
        db.session.commit()
    return render_template('mis_pedidos.html', orders=orders)

# ============ ADMIN ROUTES ============
@app.route('/admin/caja')
@login_required
@admin_required
def admin_caja():
    categories = Category.query.all()
    debt_id = request.args.get('debt_id')
    debt_client = None
    if debt_id:
        debt_client = Debt.query.get(debt_id)
    return render_template('admin/caja.html', categories=categories, debt_client=debt_client)

@app.route('/admin/ventas')
@login_required
@admin_required
def admin_ventas():
    from sqlalchemy import func
    today = datetime.utcnow().date()
    sales = Sale.query.filter(func.date(Sale.created_at) == today).order_by(Sale.created_at.desc()).all()
    total_sales = sum(s.total for s in sales)
    cash_total = sum(s.total for s in sales if s.payment_method == 'efectivo')
    transfer_total = sum(s.total for s in sales if s.payment_method == 'transferencia')
    return render_template('admin/ventas.html', sales=sales, total_sales=total_sales, cash_total=cash_total, transfer_total=transfer_total)

@app.route('/admin/ventas/export')
@login_required
@admin_required
def admin_ventas_export():
    sales = Sale.query.order_by(Sale.created_at.desc()).all()
    data = []
    for s in sales:
        data.append({
            'ID': s.id,
            'Fecha': s.created_at.strftime('%Y-%m-%d %H:%M'),
            'Cliente': s.client_name,
            'Total': s.total,
            'Método Pago': s.payment_method
        })
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Ventas')
    output.seek(0)
    
    from flask import send_file
    return send_file(
        output,
        download_name='ventas_catashop.xlsx',
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/admin/historial')
@login_required
@admin_required
def admin_historial():
    sales = Sale.query.order_by(Sale.created_at.desc()).all()
    return render_template('admin/historial.html', sales=sales)

@app.route('/admin/deudas')
@login_required
@admin_required
def admin_deudas():
    debts = Debt.query.all()
    total_debt = sum(d.debt for d in debts if d.status == 'pending')
    clients_count = len(debts)
    pending_count = len([d for d in debts if d.status == 'pending'])
    return render_template('admin/deudas.html', debts=debts, total_debt=total_debt, clients_count=clients_count, pending_count=pending_count)

@app.route('/admin/pedidos')
@login_required
@admin_required
def admin_pedidos():
    orders = Order.query.order_by(Order.created_at.desc()).all()
    return render_template('admin/pedidos.html', orders=orders)

@app.route('/admin/configuracion')
@login_required
@admin_required
def admin_configuracion():
    return render_template('admin/config_dashboard.html')

@app.route('/admin/ajustes', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_ajustes():
    config = StoreConfig.query.first()
    if not config:
        config = StoreConfig()
        db.session.add(config)
        db.session.commit()
    if request.method == 'POST':
        # General
        config.store_name = request.form.get('store_name', config.store_name)
        config.admin_name = request.form.get('admin_name', config.admin_name)
        config.ticket_message = request.form.get('ticket_message', config.ticket_message)
        config.printer_name = request.form.get('printer_name', '') or ''
        config.auto_print = 'auto_print' in request.form
        config.auto_drawer = 'auto_drawer' in request.form
        # Fiscal
        config.razon_social = request.form.get('razon_social', '')
        config.nit = request.form.get('nit', '')
        config.rut = request.form.get('rut', '')
        config.regimen_tributario = request.form.get('regimen_tributario', 'No responsable de IVA')
        config.gran_contribuyente = request.form.get('gran_contribuyente', '')
        config.agente_retencion = request.form.get('agente_retencion', '')
        # DIAN
        config.resolucion_numero = request.form.get('resolucion_numero', '')
        config.resolucion_vigencia = request.form.get('resolucion_vigencia', '')
        config.resolucion_rango_desde = request.form.get('resolucion_rango_desde', '')
        config.resolucion_rango_hasta = request.form.get('resolucion_rango_hasta', '')
        # Hero
        config.hero_title_white = request.form.get('hero_title_white', '')
        config.hero_title_pink = request.form.get('hero_title_pink', '')
        config.hero_subtitle = request.form.get('hero_subtitle', '')
        # Handle logo upload
        if 'logo' in request.files:
            file = request.files['logo']
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                file.save(filepath)
                config.logo = '/' + filepath.replace('\\', '/')
        # Handle QR upload
        if 'qr_transfer' in request.files:
            file = request.files['qr_transfer']
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                filepath = os.path.join(UPLOAD_FOLDER, 'qr_' + filename)
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                file.save(filepath)
                config.qr_transfer = '/' + filepath.replace('\\', '/')
        # Handle hero image upload
        if 'hero_image' in request.files:
            file = request.files['hero_image']
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                filepath = os.path.join(UPLOAD_FOLDER, 'hero_' + filename)
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                file.save(filepath)
                config.hero_image = '/' + filepath.replace('\\', '/')
        db.session.commit()
        flash('Configuración actualizada correctamente', 'success')
        return redirect(url_for('admin_ajustes'))
    return render_template('admin/configuracion.html', config=config)

@app.route('/admin/inventario')
@login_required
@admin_required
def admin_inventario():
    products = Product.query.all()
    categories = Category.query.all()
    return render_template('admin/inventario.html', products=products, categories=categories)

@app.route('/admin/categorias')
@login_required
@admin_required
def admin_categorias():
    categories = Category.query.all()
    return render_template('admin/categorias.html', categories=categories)

# ============ ADMIN API ROUTES ============
@app.route('/admin/api/pos/search')
@login_required
@admin_required
def api_pos_search():
    q = request.args.get('q', '')
    products = Product.query.filter(
        db.or_(Product.name.ilike(f'%{q}%'), Product.barcode.ilike(f'%{q}%'))
    ).limit(10).all()
    result = []
    for p in products:
        sizes = {}
        if p.sizes:
            try:
                sizes = json.loads(p.sizes)
            except:
                sizes = {}
        result.append({'id': p.id, 'name': p.name, 'price': p.price, 'price_formatted': "${:,.0f}".format(p.price).replace(",", "."), 'image': p.image, 'sizes': sizes, 'barcode': p.barcode, 'stock': p.stock})
    
    if not result:
        return jsonify({'success': False, 'message': 'No se encontraron productos'})
        
    return jsonify({'success': True, 'products': result})


@app.route('/admin/api/pos/products-by-category/<int:cid>')
@login_required
@admin_required
def api_pos_products(cid):
    products = Product.query.filter_by(category_id=cid, active=True).all()
    result = []
    for p in products:
        sizes = {}
        if p.sizes:
            try:
                sizes = json.loads(p.sizes)
            except:
                sizes = {s.strip(): 10 for s in p.sizes.split(',') if s.strip()}
        result.append({
            'id': p.id,
            'name': p.name,
            'price': p.price,
            'price_formatted': "${:,.0f}".format(p.price).replace(",", "."),
            'image': p.image,
            'sizes': sizes,
            'barcode': p.barcode,
            'stock': p.stock
        })
    return jsonify({'success': True, 'products': result})

@app.route('/admin/api/pos/complete', methods=['POST'])
@login_required
@admin_required
def api_pos_complete():
    data = request.get_json()
    items = data.get('items', [])
    payment_info = data.get('payment', {})
    payment_method = payment_info.get('method', 'efectivo')
    client_name = data.get('client_name', '')
    if not client_name and payment_info.get('client_name'):
        client_name = payment_info.get('client_name')
        
    is_debt = (payment_method == 'fiado' or data.get('is_debt', False))

    if not items:
        return jsonify({'success': False, 'message': 'No hay productos'}), 400

    total = 0
    sale = Sale(total=0, payment_method=payment_method, client_name=client_name)
    db.session.add(sale)
    db.session.flush()

    for item in items:
        product = Product.query.get(item['product_id'])
        if product:
            item_total = product.price * item['quantity']
            total += item_total
            si = SaleItem(
                sale_id=sale.id,
                product_id=product.id,
                quantity=item['quantity'],
                size=item.get('size', ''),
                price=product.price
            )
            db.session.add(si)

            # Decrease stock if sizes are tracked
            if product.sizes:
                try:
                    sizes = json.loads(product.sizes)
                    s = item.get('size', '')
                    if s in sizes:
                        sizes[s] = max(0, sizes[s] - item['quantity'])
                    product.sizes = json.dumps(sizes)
                except:
                    pass

    sale.total = total

    # Handle debt
    if is_debt and client_name:
        debt = Debt.query.filter_by(client_name=client_name, status='pending').first()
        if debt:
            debt.total_bought += total
            debt.debt = debt.total_bought - debt.total_paid
        else:
            debt = Debt(client_name=client_name, total_bought=total, debt=total)
            db.session.add(debt)

    db.session.commit()
    
    # Get store config for receipt printing
    config = StoreConfig.query.first()
    amount_paid = data.get('amount_paid', total)
    change = max(0, float(amount_paid) - total) if payment_method == 'efectivo' else 0
    order_number = f'CS-{sale.id:04d}'

    return jsonify({
        'success': True,
        'sale': {
            'id': sale.id,
            'receipt_number': order_number,
            'change': change,
            'total': total
        },
        'store_name': config.store_name if config else 'Cata Shoope',
        'printer_name': config.printer_name if config else '',
        'auto_print': config.auto_print if config else False,
        'auto_drawer': config.auto_drawer if config else False,
        'ticket_message': config.ticket_message if config else '¡Gracias por su compra!',
        'razon_social': config.razon_social if config else '',
        'nit': config.nit if config else ''
    })

@app.route('/admin/api/credit-sale', methods=['POST'])
@login_required
@admin_required
def api_credit_sale():
    data = request.get_json()
    client_id = data.get('client_id')
    items = data.get('items', [])
    
    if not items:
        return jsonify({'success': False, 'message': 'No hay productos'}), 400
    
    debt = Debt.query.get(client_id) if client_id else None
    client_name = debt.client_name if debt else 'Cliente Fiado'
    
    total = 0
    sale = Sale(total=0, payment_method='fiado', client_name=client_name)
    db.session.add(sale)
    db.session.flush()
    
    for item in items:
        if item.get('is_custom'):
            item_total = float(item.get('custom_price', 0)) * int(item.get('quantity', 1))
            total += item_total
            si = SaleItem(sale_id=sale.id, product_id=None, quantity=item['quantity'], price=float(item['custom_price']))
            db.session.add(si)
        else:
            product = Product.query.get(item['product_id'])
            if product:
                item_total = product.price * item['quantity']
                total += item_total
                si = SaleItem(sale_id=sale.id, product_id=product.id, quantity=item['quantity'], size=item.get('size', ''), price=product.price)
                db.session.add(si)
                # Decrease stock
                if product.sizes:
                    try:
                        sizes = json.loads(product.sizes)
                        s = item.get('size', '')
                        if s in sizes:
                            sizes[s] = max(0, sizes[s] - item['quantity'])
                        product.sizes = json.dumps(sizes)
                    except:
                        pass
    
    sale.total = total
    
    # Update debt record
    if debt:
        debt.total_bought += total
        debt.debt = debt.total_bought - debt.total_paid
    
    db.session.commit()
    order_number = f'CS-{sale.id:04d}'
    
    return jsonify({
        'success': True,
        'sale': {
            'id': sale.id,
            'receipt_number': order_number,
            'total': total
        }
    })

# ============ HARDWARE API ============
@app.route('/admin/api/hardware/printers', methods=['GET'])
@login_required
@admin_required
def api_hardware_printers():
    printers = []
    try:
        import win32print
        for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS):
            printers.append(p[2])
    except Exception:
        pass
    return jsonify({'success': True, 'printers': printers})

@app.route('/admin/api/hardware/print', methods=['POST'])
@login_required
@admin_required
def api_hardware_print():
    data = request.get_json()
    printer_name = data.get('printer_name', '')
    raw_data = data.get('raw_data', '')
    if not printer_name or not raw_data:
        return jsonify({'success': False, 'message': 'Datos incompletos'})
    try:
        import win32print
        hPrinter = win32print.OpenPrinter(printer_name)
        try:
            win32print.StartDocPrinter(hPrinter, 1, ("Receipt", None, "RAW"))
            win32print.StartPagePrinter(hPrinter)
            win32print.WritePrinter(hPrinter, raw_data.encode('utf-8'))
            win32print.EndPagePrinter(hPrinter)
            win32print.EndDocPrinter(hPrinter)
        finally:
            win32print.ClosePrinter(hPrinter)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/admin/api/hardware/open-drawer', methods=['POST'])
@login_required
@admin_required
def api_hardware_open_drawer():
    data = request.get_json()
    printer_name = data.get('printer_name', '')
    if not printer_name:
        return jsonify({'success': False, 'message': 'No hay impresora configurada'})
    try:
        import win32print
        hPrinter = win32print.OpenPrinter(printer_name)
        try:
            win32print.StartDocPrinter(hPrinter, 1, ("Drawer", None, "RAW"))
            win32print.StartPagePrinter(hPrinter)
            win32print.WritePrinter(hPrinter, b'\x1B\x70\x00\x19\xFA')
            win32print.EndPagePrinter(hPrinter)
            win32print.EndDocPrinter(hPrinter)
        finally:
            win32print.ClosePrinter(hPrinter)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/admin/api/product/add', methods=['POST'])
@login_required
@admin_required
def api_product_add():
    name = request.form.get('name')
    price = float(request.form.get('price', 0))
    category_id = int(request.form.get('category_id'))
    description = request.form.get('description', '')
    sizes = request.form.get('sizes', '{}')

    if price < 0:
        return jsonify({'error': 'El precio no puede ser negativo'}), 400

    product = Product(name=name, price=price, category_id=category_id, description=description, sizes=sizes)

    if 'image' in request.files:
        file = request.files['image']
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = f"{timestamp}_{filename.rsplit('.', 1)[0]}.webp"
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            optimized = optimize_image(file)
            if isinstance(optimized, BytesIO):
                with open(filepath, 'wb') as f:
                    f.write(optimized.read())
            else:
                optimized.save(filepath)
                
            product.image = '/' + filepath.replace('\\', '/')

    db.session.add(product)
    db.session.commit()
    return jsonify({'success': True, 'id': product.id})

@app.route('/admin/api/product/<int:pid>/update', methods=['POST'])
@login_required
@admin_required
def api_product_update(pid):
    product = Product.query.get_or_404(pid)
    product.name = request.form.get('name', product.name)
    
    new_price = float(request.form.get('price', product.price))
    if new_price < 0:
        return jsonify({'error': 'El precio no puede ser negativo'}), 400
    product.price = new_price
    
    product.category_id = int(request.form.get('category_id', product.category_id))
    product.description = request.form.get('description', product.description)
    if request.form.get('sizes'):
        product.sizes = request.form.get('sizes')

    if 'image' in request.files:
        file = request.files['image']
        if file and file.filename and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = f"{timestamp}_{filename.rsplit('.', 1)[0]}.webp"
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            optimized = optimize_image(file)
            if isinstance(optimized, BytesIO):
                with open(filepath, 'wb') as f:
                    f.write(optimized.read())
            else:
                optimized.save(filepath)
                
            product.image = '/' + filepath.replace('\\', '/')

    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/api/product/<int:pid>/delete', methods=['POST'])
@login_required
@admin_required
def api_product_delete(pid):
    product = Product.query.get_or_404(pid)
    db.session.delete(product)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/api/category/add', methods=['POST'])
@login_required
@admin_required
def api_category_add():
    data = request.get_json()
    cat = Category(name=data['name'], gender=data.get('gender', 'Mujer'))
    db.session.add(cat)
    db.session.commit()
    return jsonify({'success': True, 'id': cat.id})

@app.route('/admin/api/category/<int:cid>/delete', methods=['POST'])
@login_required
@admin_required
def api_category_delete(cid):
    cat = Category.query.get_or_404(cid)
    if cat.products:
        return jsonify({'error': 'La categoria tiene productos'}), 400
    db.session.delete(cat)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/api/order/<int:oid>/confirm', methods=['POST'])
@login_required
@admin_required
def api_order_confirm(oid):
    order = Order.query.get_or_404(oid)
    data = request.get_json() or {}
    order.status = 'confirmed'
    if data.get('admin_message'):
        order.admin_message = data['admin_message']
    if data.get('payment_method'):
        order.payment_method = data['payment_method']
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/api/order/<int:oid>/cancel', methods=['POST'])
@login_required
@admin_required
def api_order_cancel(oid):
    order = Order.query.get_or_404(oid)
    order.status = 'cancelled'
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/api/order/<int:oid>/complete', methods=['POST'])
@login_required
@admin_required
def api_order_complete(oid):
    order = Order.query.get_or_404(oid)
    order.status = 'completed'
    data = request.get_json() or {}
    if data.get('send_email') and order.client_email:
        send_invoice_email(order, order.client_email)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/api/debt/add', methods=['POST'])
@login_required
@admin_required
def api_debt_add():
    data = request.get_json()
    debt = Debt(
        client_name=data['client_name'],
        phone=data.get('phone', ''),
        total_bought=float(data.get('total_bought', 0)),
        total_paid=float(data.get('total_paid', 0)),
        debt=float(data.get('total_bought', 0)) - float(data.get('total_paid', 0))
    )
    db.session.add(debt)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/api/debt/<int:did>/pay', methods=['POST'])
@login_required
@admin_required
def api_debt_pay(did):
    data = request.get_json()
    debt = Debt.query.get_or_404(did)
    amount = float(data.get('amount', 0))
    debt.total_paid += amount
    debt.debt = debt.total_bought - debt.total_paid
    if debt.debt <= 0:
        debt.status = 'paid'
        debt.debt = 0
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/api/debt/<int:did>/delete', methods=['POST'])
@login_required
@admin_required
def api_debt_delete(did):
    debt = Debt.query.get_or_404(did)
    db.session.delete(debt)
    db.session.commit()
    return jsonify({'success': True})

@app.route('/admin/api/sale/<int:sid>/delete', methods=['POST'])
@login_required
@admin_required
def api_sale_delete(sid):
    sale = Sale.query.get_or_404(sid)
    SaleItem.query.filter_by(sale_id=sid).delete()
    db.session.delete(sale)
    db.session.commit()
    return jsonify({'success': True})

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    app.run(debug=True, port=5001)