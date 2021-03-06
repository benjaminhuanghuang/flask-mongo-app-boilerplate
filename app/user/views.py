import os
from werkzeug.utils import secure_filename
from mongoengine import Q

from flask import Blueprint, render_template, request, redirect, session, url_for, abort, current_app
import bcrypt
import uuid  # for user resigistration email confirm
#
from .models import User
from .forms import RegisterForm, LoginForm, EditForm, ForgotForm, PasswordResetForm
from ..utilities.emailsender import send_email
from ..utilities.imaging import thumbnail_process
from ..relationship.models import Relationship

from ..relationship.models import Relationship
from .decorators import login_required

# display fee form in profile view
from ..feed.forms import FeedPostForm
from ..feed.models import Message, POST

user_app = Blueprint('user_app', __name__)


@user_app.route('/register', methods=('GET', 'POST'))
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        salt = bcrypt.gensalt()
        hashed_password = bcrypt.hashpw(form.password.data, salt)
        confirmation_code = str(uuid.uuid4())

        user = User(
            username=form.username.data,
            password=hashed_password,
            email=form.email.data,
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            change_configuration={
                'new_email': form.email.data.lower(),
                'confirmation_code': confirmation_code
            }
        )
        # email user
        body_html = render_template('mail/user/register.html', user=user)
        body_text = render_template('mail/user/register.txt', user=user)
        send_email("Welcome to my flask app boilerplate", user.email, None, body_html, body_text, None)

        user.save()
        return "User registered"
    return render_template('user/register.html', form=form)


@user_app.route('/login', methods=('GET', 'POST'))
def login():
    form = LoginForm()
    error = None

    if request.method == 'GET' and request.args.get('next'):
        session['next'] = request.args.get('next')

    if form.validate_on_submit():
        user = User.objects.filter(
            username=form.username.data
        ).first()
        if user:
            if bcrypt.hashpw(form.password.data, user.password) == user.password:
                session['username'] = form.username.data
                if 'next' in session:
                    next = session.get('next')
                    session.pop('next')
                    return redirect(next)
                else:
                    return 'User logged in'
            else:
                user = None
        if not user:
            error = 'Incorrect credentials'
    return render_template('user/login.html', form=form, error=error)


@user_app.route('/logout')
def logout():
    session.pop('username')
    return redirect(url_for('user_app.login'))


@user_app.route('/<username>/friends/<int:page>')
@user_app.route('/<username>/friends', endpoint='profile-friends')  # the url alias is "profile-friends"
@user_app.route('/<username>')
def profile(username, page=1):
    logged_user = None
    rel = None
    friends_page = False   #
    user = User.objects.filter(username=username).first()
    profile_messages = []

    if user:
        if session.get('username'):
            logged_user = User.objects.filter(username=session.get('username')).first()
            rel = Relationship.get_relationship(logged_user, user)

        # get friends
        friends = Relationship.objects.filter(
            from_user=user,
            rel_type=Relationship.FRIENDS,
            status=Relationship.APPROVED
            )
        friends_total = friends.count()

        if 'friends' in request.url:
            friends_page = True
            friends = friends.paginate(page=page, per_page=3)
        else:
            friends = friends[:5]

        form = FeedPostForm()

        # get user messages if friends or self
        if logged_user and (rel == "SAME" or rel == "FRIENDS_APPROVED"):
            profile_messages = Message.objects.filter(
                Q(from_user=user) | Q(to_user=user),
                message_type=POST
                ).order_by('-create_date')[:10]

        return render_template('user/profile.html',
            user=user,
            logged_user=logged_user,
            rel=rel,
            friends=friends,
            friends_total=friends_total,
            friends_page=friends_page,
            form=form,
            profile_messages=profile_messages
            )
    else:
        abort(404)


@user_app.route('/edit', methods=('GET', 'POST'))
@login_required
def edit():
    error = None
    message = None
    user = User.objects.filter(username=session.get('username')).first()
    if user:
        form = EditForm(obj=user)
        if form.validate_on_submit():
            # check image
            image_url = None
            if request.files.get('image'):
                filename = secure_filename(form.image.data.filename)
                # save image
                file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'user', filename)
                form.image.data.save(file_path)
                # create thumbnail and url
                image_url = str(thumbnail_process(file_path, 'user', str(user.id)))

            if user.username != form.username.data.lower():
                if User.objects.filter(username=form.username.data.lower()).first():
                    error = "Username already exists"
                else:
                    session['username'] = form.username.data.lower()
                    form.username.data = form.username.data.lower()
            if user.email != form.email.data:
                if User.objects.filter(email=form.email.data.lower()).first():
                    error = "Email already exists"
                else:
                    # Confirm new email address
                    code = str(uuid.uuid4())

                    user.change_configuration = {
                        "new_email": form.email.data.lower(),
                        "confirmation_code": code
                    }
                    user.email_confirmed = False
                    form.email.data = user.email
                    message = "You will need to confirm the new email to complete this change"

                    # email the user
                    body_html = render_template('mail/user/change_email.html', user=user)
                    body_text = render_template('mail/user/change_email.txt', user=user)
                    send_email("Confirm your new email", user.change_configuration['new_email'], None,
                               body_html, body_text, None)

            if not error:
                form.populate_obj(user)
                if image_url:
                    user.avatar = image_url
                user.save()
                if not message:  # Important! do not overwrite email confirm message
                    message = "Profile updated"
        return render_template("user/edit.html", form=form, error=error, message=message, user=user)
    else:
        abort(404)


# Response the confirm link in the register confirm email
# Compare the confirm code in the link and the confirm code in data base
# Set user.email_confirmed
# Display email_confirm page
@user_app.route('/confirm/<username>/<code>', methods=('GET', 'POST'))
def confirm(username, code):
    user = User.objects.filter(username=username).first()
    if user and user.change_configuration and user.change_configuration.get('confirmation_code'):
        if code == user.change_configuration.get('confirmation_code'):
            user.email = user.change_configuration.get('new_email')
            user.change_configuration = {}
            user.email_confirmed = True
            user.save()
            return render_template('user/email_confirmed.html')
    else:
        abort(404)


@user_app.route('/forgot', methods=('GET', 'POST'))
def forgot():
    error = None
    message = None
    form = ForgotForm()
    if form.validate_on_submit():
        user = User.objects.filter(email=form.email.data.lower()).first()
        if user:
            code = str(uuid.uuid4())
            user.change_configuration = {
                "password_reset_code": code
            }
            user.save()

            # email the user
            body_html = render_template('mail/user/password_reset.html', user=user)
            body_text = render_template('mail/user/password_reset.txt', user=user)
            send_email("Password reset request", user.email, None, body_html, body_text, None)

        message = "You will receive a password reset email if we find that email in our system"
    return render_template('user/forgot.html', form=form, error=error, message=message)


@user_app.route('/password_reset/<username>/<code>', methods=('GET', 'POST'))
def password_reset(username, code):
    message = None
    require_current = None

    form = PasswordResetForm()

    user = User.objects.filter(username=username).first()
    if not user or code != user.change_configuration.get('password_reset_code'):
        abort(404)

    if request.method == 'POST':
        del form.current_password
        if form.validate_on_submit():
            salt = bcrypt.gensalt()
            hashed_password = bcrypt.hashpw(form.password.data, salt)
            user.password = hashed_password
            user.change_configuration = {}
            user.save()

            if session.get('username'):
                session.pop('username')
            return redirect(url_for('user_app.password_reset_complete'))

    return render_template('user/password_reset.html',
                           form=form, message=message, require_current=require_current,
                           username=username, code=code)


@user_app.route('/password_reset_complete')
def password_reset_complete():
    return render_template('user/password_change_confirmed.html')


@user_app.route('/change_password', methods=('GET', 'POST'))
def change_password():
    require_current = True  #
    error = None
    form = PasswordResetForm()
    # Search current in database
    user = User.objects.filter(username=session.get('username')).first()

    if not user:
        abort(404)

    if request.method == 'POST':
        if form.validate_on_submit():
            if bcrypt.hashpw(form.current_password.data, user.password) == user.password:
                salt = bcrypt.gensalt()
                hashed_password = bcrypt.hashpw(form.password.data, salt)
                user.password = hashed_password
                user.save()
                # if user is logged in, log him out
                if session.get('username'):
                    session.pop('username')
                return redirect(url_for('user_app.password_reset_complete'))
            else:
                error = "Incorrect password"
    return render_template('user/password_reset.html',
                           form=form, require_current=require_current, error=error)
