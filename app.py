from flask import Flask, request, render_template_string, redirect, url_for, render_template, session, flash, send_file, Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import enum
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from psd_tools import PSDImage
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display
import pandas as pd
import os
import io


app = Flask(__name__)
app.secret_key = 'xyzsdfga'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///maindb.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}


if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)  # افزایش طول برای هش رمز عبور



@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        existing_user = User.query.filter_by(username=username).first()

        if existing_user:
            flash('This username is already taken. Please choose another one.')
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(password)

        new_user = User(username=username, password=hashed_password)
        db.session.add(new_user)
        try:
            db.session.commit()
            flash('Account created successfully! Please login.')
            return redirect(url_for('login'))
        except IntegrityError:
            db.session.rollback()
            flash('An error occurred. Please try again.')
            return redirect(url_for('register'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['loggedin'] = True
            session['userid'] = user.id
            session['username'] = user.username
            return redirect(url_for('admin'))
        else:
            return 'Invalid username/password!'
    return render_template('login.html')

@app.route('/admin')
def admin():
    if not session.get('loggedin'):
        return redirect(url_for('login'))
    products = Product.query.all()
    return render_template('admin.html', products=products)

@app.route('/add_serial', methods=['POST'])
def add_serial():
    serial_code = request.form['serial_code']
    # بررسی اینکه آیا سریال قبلاً در دیتابیس وجود دارد
    existing_product = Product.query.filter_by(serial_code=serial_code).first()

    # اگر سریال قبلاً وجود داشته باشد، پیام خطا نمایش داده می‌شود
    if existing_product is not None:
        flash('کد سریال تکراری است. لطفاً با یک کد سریال منحصر به فرد دوباره امتحان کنید', 'error')
        return redirect(url_for('admin'))

    # ایجاد و اضافه کردن محصول جدید به دیتابیس
    new_product = Product(serial_code=serial_code, status='unused', image_path='', text_content='')
    db.session.add(new_product)
    try:
        db.session.commit()
        flash('!سریال با موفقیت اضافه شد', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error adding serial code. Please try again.', 'error')

    return redirect(url_for('admin'))



@app.route('/logout')
def logout():
    session.pop('loggedin', None)
    session.pop('userid', None)
    session.pop('username', None)
    return redirect(url_for('login'))


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    serial_code = db.Column(db.String(20), unique=True, nullable=False)
    status = db.Column(db.String(10), nullable=False)
    image_path = db.Column(db.String(100))  # مسیر فایل عکس موجود
    text_content = db.Column(db.String(300))  # متن
    export_image_path = db.Column(db.String(100))  # مسیر فایل عکس خروجی پردازش شده



@app.route('/products')
def show_products():
    products = Product.query.all()
    products_list = '<ul>'
    for product in products:
        products_list += f'<li>{product.serial_code} - {product.status}</li>'
    products_list += '</ul>'
    return products_list


@app.route('/authenticate', methods=['GET', 'POST'])
def authenticate():
    if request.method == 'POST':
        serial_code = request.form['serial_code']
        product = Product.query.filter_by(serial_code=serial_code).first()
        
        if product:
            if product.status == 'unused':
                # اگر سریال unused است، کاربر را به صفحه‌ای برای وارد کردن اطلاعات هدایت کنید
                return redirect(url_for('user_page', serial_code=serial_code))
            else:
                # اگر سریال used است، کاربر را به صفحه‌ای برای مشاهده اطلاعات (بدون امکان ویرایش) هدایت کنید
                flash('سریال استفاده شده. شما تنها میتوانید آن را مشاهده کنید')
                return redirect(url_for('view_product', serial_code=serial_code))
        else:
            flash('سریال نامعتبر')
            
    return render_template('authenticate.html')



def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/user_page/<serial_code>', methods=['GET', 'POST'])
def user_page(serial_code):
    product = Product.query.filter_by(serial_code=serial_code).first_or_404()
    editable = product.status == 'unused'  # وضعیت قابل ویرایش بودن را بررسی می‌کند

    if request.method == 'POST' and editable:
        file = request.files.get('image', None)
        text_content = request.form.get('text_content', '')  # دریافت متن ورودی از فرم

        if text_content:  # اطمینان از اینکه متن ورودی خالی نیست
            product.text_content = text_content  # به‌روزرسانی متن در مدل Product

        if file and file.filename:
            if allowed_file(file.filename):
                filename = secure_filename(file.filename)
                # ذخیره فایل در پوشه مشخص شده
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename).replace('\\', '/')
                file.save(file_path)
                # تبدیل کاراکترهای \ به / در مسیر ذخیره شده
                relative_path = os.path.join('uploads', filename).replace('\\', '/')
                product.image_path = relative_path


                # ارسال اطلاعات به تابع پردازش تصویر و ذخیره مسیر تصویر پردازش شده در دیتابیس
                processed_image_path = process_image(text_content, file_path, serial_code)
                product.export_image_path = processed_image_path

                # تغییر وضعیت به 'used'
                product.status = 'used'
                db.session.commit()  # ذخیره تغییرات در دیتابیس

                flash('Product updated successfully and marked as used.')
                return redirect(url_for('view_product', serial_code=serial_code))
            else:
                allowed_formats = ", ".join(ALLOWED_EXTENSIONS)
                flash(f'Invalid file format. Only {allowed_formats} are allowed.')
                return redirect(request.url)

        else:
            # اگر فایلی انتخاب نشده باشد، فقط متن به‌روزرسانی می‌شود
            db.session.commit()
            flash('Text content updated successfully.')

    elif request.method == 'POST' and not editable:
        flash('This serial code has been used and cannot be edited.')

    return render_template('user_page.html', product=product, editable=editable)




@app.route('/edit_product/<int:product_id>', methods=['GET', 'POST'])
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)
    if request.method == 'POST':
        product.serial_code = request.form['serial_code']
        product.status = request.form['status']
        product.text_content = request.form['text_content']
        file = request.files['image']
        
        if file and allowed_file(file.filename):
            # اگر فایل جدیدی آپلود شده باشد، آن را ذخیره و مسیر جدید را ذخیره کنید
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            product.image_path = filename  # ذخیره مسیر تصویر اصلی
        else:
            # اگر فایل جدیدی آپلود نشده، از مسیر تصویر فعلی استفاده کنید
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], product.image_path)

        # فراخوانی تابع پردازش تصویر با داده‌های جدید و ذخیره مسیر تصویر جدید
        processed_image_path = process_image(product.text_content, file_path, product.serial_code)
        product.export_image_path = processed_image_path  # به‌روزرسانی مسیر تصویر پردازش شده در دیتابیس
        
        db.session.commit()
        flash('Product updated successfully, including the image!')
        return redirect(url_for('admin'))
    return render_template('edit_product.html', product=product)


@app.route('/view_product/<serial_code>')
def view_product(serial_code):
    product = Product.query.filter_by(serial_code=serial_code).first_or_404()
    # رندر کردن صفحه با اطلاعات محصول بدون ارائه امکان ویرایش
    return render_template('view_product.html', product=product)


@app.route('/delete_product/<int:product_id>', methods=['POST'])
def delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    flash('Product deleted successfully!')
    return redirect(url_for('admin'))



@app.route('/download_excel')
def download_excel():
    products_query = Product.query.all()
    products_list = [{
        'Serial Code': p.serial_code, 'Status': p.status, 
        'Image Path': p.image_path, 'Text Content': p.text_content, 
        'Export Image Path': p.export_image_path
    } for p in products_query]
    
    df = pd.DataFrame(products_list)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')
    output.seek(0)
    
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     download_name='products.xlsx', as_attachment=True)


def process_image(input_name, input_image_path, input_serial):
    # باز کردن فایل PSD و تولید تصویر نهایی
    current_directory = os.path.dirname(__file__)

    # مسیر فایل psd در دایرکتوری فعلی
    psd_file_path = os.path.join(current_directory, 'editor-test.psd')

    psd = PSDImage.open(psd_file_path)

    image = psd.compose()

    # بارگذاری تصویر و تغییر اندازه آن به 330x330 پیکسل
    overlay_image = Image.open(input_image_path).resize((330, 330))

    # ایجاد ماسک دایره‌ای برای گرد کردن گوشه‌ها
    mask = Image.new('L', (330, 330), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, 330, 330), fill=255)
    overlay_image.putalpha(mask)

    # تعیین موقعیت و قرار دادن تصویر دایره‌ای شکل روی تصویر اصلی
    position = (300, 41)
    image.paste(overlay_image, position, mask)

    # تبدیل تصویر به حالت RGB
    image_rgb = image.convert('RGB')

    # ایجاد یک نمونه از ImageDraw برای اضافه کردن متن
    draw = ImageDraw.Draw(image_rgb)

    # آدرس دایرکتوری فعلی
    current_directory = os.path.dirname(__file__)

    # آدرس فونت
    font_path = os.path.join(current_directory, 'Vazir-Medium.ttf')

    # بارگذاری فونت
    font = ImageFont.truetype(font_path, 36)
    serial_font = ImageFont.truetype(font_path, 30)

    # بازآرایی متن فارسی برای نمایش صحیح - برای input_name
    reshaped_text = arabic_reshaper.reshape(input_name)
    bidi_text = get_display(reshaped_text)

    # اضافه کردن متن به تصویر - برای input_name
    draw.text((720, 875), bidi_text, fill="#4f4f4f", anchor="rt")

    # بازآرایی و اضافه کردن متن برای input_serial
    reshaped_serial = arabic_reshaper.reshape(input_serial)
    bidi_serial = get_display(reshaped_serial)

    # موقعیت برای input_serial - 30 پیکسل پایین‌تر
    serial_position = (735, 1000)  # موقعیت جدید برای input_serial

    # اضافه کردن input_serial به تصویر
    draw.text(serial_position, bidi_serial, fill="#4f4f4f", anchor="rt")

    relative_export_path = os.path.join('export', f'final_output_{input_serial}.png')

    # مسیر کامل فیزیکی برای ذخیره‌سازی تصویر نهایی بر روی سرور
    full_export_path = os.path.join(app.static_folder, relative_export_path)

    # اطمینان از ایجاد شدن پوشه 'export'
    if not os.path.exists(os.path.dirname(full_export_path)):
        os.makedirs(os.path.dirname(full_export_path))

    # ذخیره‌سازی تصویر نهایی
    image_rgb.save(full_export_path, 'PNG')

    # بازگرداندن مسیر نسبی برای ذخیره در دیتابیس
    return relative_export_path.replace('\\', '/')




@app.route('/')
def index():
    return "Hello, World!"

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0')
