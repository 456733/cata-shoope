from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    phone = db.Column(db.String(50))
    password_hash = db.Column(db.String(300), nullable=False)
    role = db.Column(db.String(50), default='client')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_verified = db.Column(db.Boolean, default=False)
    verification_code = db.Column(db.String(10))
    reset_token = db.Column(db.String(150))

    @property
    def username(self):
        return self.name

    @property
    def password(self):
        return self.password_hash

class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    gender = db.Column(db.String(50))
    image = db.Column(db.String(300))
    description = db.Column(db.Text)
    active = db.Column(db.Boolean, default=True)
    products = db.relationship('Product', backref='category', lazy=True)

class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    barcode = db.Column(db.String(100), index=True)
    description = db.Column(db.Text)
    price = db.Column(db.Float, nullable=False)
    cost = db.Column(db.Float, default=0)
    image = db.Column(db.String(300))
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False, index=True)
    gender = db.Column(db.String(50))
    sizes = db.Column(db.Text)
    stock = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(50))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    client_name = db.Column(db.String(200))
    status = db.Column(db.String(50), default='pending')
    order_type = db.Column(db.String(50))
    payment_method = db.Column(db.String(50))
    subtotal = db.Column(db.Float, default=0)
    total = db.Column(db.Float, nullable=False)
    amount_paid = db.Column(db.Float, default=0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)
    client_phone = db.Column(db.String(50))
    client_email = db.Column(db.String(120))
    client_address = db.Column(db.Text)
    delivery_type = db.Column(db.String(50))
    payment_preference = db.Column(db.String(50))
    admin_message = db.Column(db.Text)
    is_message_read = db.Column(db.Boolean, default=False)
    items = db.relationship('OrderItem', backref='order', lazy=True)
    user = db.relationship('User', backref='orders')

class OrderItem(db.Model):
    __tablename__ = 'order_items'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('orders.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    product_name = db.Column(db.String(200))
    size = db.Column(db.String(10))
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, default=0)
    product = db.relationship('Product')

class Sale(db.Model):
    __tablename__ = 'sale'
    id = db.Column(db.Integer, primary_key=True)
    total = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    client_name = db.Column(db.String(200))
    items = db.relationship('SaleItem', backref='sale', lazy=True)

class SaleItem(db.Model):
    __tablename__ = 'sale_item'
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sale.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    size = db.Column(db.String(10))
    price = db.Column(db.Float, nullable=False)
    product = db.relationship('Product')

class Debt(db.Model):
    __tablename__ = 'debts'
    id = db.Column(db.Integer, primary_key=True)
    client_name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(50))
    total_bought = db.Column(db.Float, default=0)
    total_paid = db.Column(db.Float, default=0)
    status = db.Column(db.String(50), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def debt(self):
        return (self.total_bought or 0) - (self.total_paid or 0)

class StoreConfig(db.Model):
    __tablename__ = 'store_config'
    id = db.Column(db.Integer, primary_key=True)
    store_name = db.Column(db.String(200), default='Cata Shoope')
    admin_name = db.Column(db.String(200))
    logo = db.Column(db.String(200))
    qr_transfer = db.Column(db.String(200))
    ticket_message = db.Column(db.Text, default='Gracias por su compra')
    printer_name = db.Column(db.String(200))
    auto_print = db.Column(db.Boolean, default=True)
    auto_drawer = db.Column(db.Boolean, default=True)
    # Hero section
    hero_title_white = db.Column(db.String(200))
    hero_title_pink = db.Column(db.String(200))
    hero_subtitle = db.Column(db.Text)
    hero_image = db.Column(db.String(300))
    # Fiscal / Invoice data
    razon_social = db.Column(db.String(300))
    nit = db.Column(db.String(50))
    rut = db.Column(db.String(50))
    regimen_tributario = db.Column(db.String(100), default='No responsable de IVA')
    gran_contribuyente = db.Column(db.String(100))
    agente_retencion = db.Column(db.String(100))
    # DIAN Resolution
    resolucion_numero = db.Column(db.String(100))
    resolucion_vigencia = db.Column(db.String(200))
    resolucion_rango_desde = db.Column(db.String(50))
    resolucion_rango_hasta = db.Column(db.String(50))