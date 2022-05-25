import os
import re
import pathlib
import tempfile
import tarfile
import hashlib
import functools
import inspect

import docker
from flask import current_app, abort
from itsdangerous.url_safe import URLSafeSerializer
from sqlalchemy.sql import or_, and_
from CTFd.models import db, Solves, Challenges
from CTFd.utils.user import get_current_user
from CTFd.utils.modes import get_model

from .models import Dojos, DojoMembers


CHALLENGES_DIR = pathlib.Path("/var/challenges")
DOJOS_DIR = pathlib.Path("/var/dojos")
PLUGIN_DIR = pathlib.Path(__file__).parent
SECCOMP = (PLUGIN_DIR / "seccomp.json").read_text()


def get_current_challenge_id():
    user = get_current_user()
    if not user:
        return None

    docker_client = docker.from_env()
    container_name = f"user_{user.id}"

    try:
        container = docker_client.containers.get(container_name)
    except docker.errors.NotFound:
        return

    for env in container.attrs["Config"]["Env"]:
        if env.startswith("CHALLENGE_ID"):
            try:
                challenge_id = int(env[len("CHALLENGE_ID=") :])
                return challenge_id
            except ValueError:
                pass


def serialize_user_flag(account_id, challenge_id, *, secret=None):
    if secret is None:
        secret = current_app.config["SECRET_KEY"]
    serializer = URLSafeSerializer(secret)
    data = [account_id, challenge_id]
    user_flag = serializer.dumps(data)[::-1]
    return user_flag


def unserialize_user_flag(user_flag, *, secret=None):
    if secret is None:
        secret = current_app.config["SECRET_KEY"]
    user_flag = re.sub(".+?{(.+)}", r"\1", user_flag)[::-1]
    serializer = URLSafeSerializer(secret)
    account_id, challenge_id = serializer.loads(user_flag)
    return account_id, challenge_id


def challenge_paths(user, challenge, *, secret=None):
    if secret is None:
        secret = current_app.config["SECRET_KEY"]

    category_global = CHALLENGES_DIR / challenge.category / "_global"
    challenge_global = CHALLENGES_DIR / challenge.category / challenge.name / "_global"

    if category_global.exists():
        yield from category_global.iterdir()

    if challenge_global.exists():
        yield from challenge_global.iterdir()

    options = sorted(
        option
        for option in (CHALLENGES_DIR / challenge.category / challenge.name).iterdir()
        if not (option.name.startswith(".") or option.name.startswith("_"))
    )

    option_hash = hashlib.sha256(f"{secret}_{user.id}_{challenge.id}".encode()).digest()
    option = options[int.from_bytes(option_hash[:8], "little") % len(options)]
    yield from option.iterdir()


def simple_tar(path, name=None):
    f = tempfile.NamedTemporaryFile()
    t = tarfile.open(mode="w", fileobj=f)
    abs_path = os.path.abspath(path)
    t.add(abs_path, arcname=(name or os.path.basename(path)))
    t.close()
    f.seek(0)
    return f


def random_home_path(user, *, secret=None):
    if secret is None:
        secret = current_app.config["SECRET_KEY"]
    return hashlib.sha256(f"{secret}_{user.id}".encode()).hexdigest()[:16]


def dojo_route(func):
    signature = inspect.signature(func)
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        bound_args = signature.bind(*args, **kwargs)
        bound_args.apply_defaults()
        dojo = bound_args.arguments["dojo"]
        if dojo is not None:
            dojo = Dojos.query.filter_by(id=dojo).first()
            if not dojo:
                abort(404)
            user = get_current_user()
            if not dojo.public:
                user_id = user.id if user else None
                if not DojoMembers.query.filter_by(dojo_id=dojo.id, user_id=user_id).exists():
                    abort(404)
        bound_args.arguments["dojo"] = dojo
        return func(*bound_args.args, **bound_args.kwargs)
    return wrapper


def user_dojos(user):
    filters = [Dojos.public == True]
    if user:
        members = db.session.query(DojoMembers.dojo_id).filter(DojoMembers.user_id == user.id)
        filters.append(Dojos.id.in_(members.subquery()))
    return Dojos.query.filter(or_(*filters)).all()


def dojo_standings(dojo_id, fields=None):
    if fields is None:
        fields = []

    Model = get_model()
    dojo = Dojos.query.filter(Dojos.id == dojo_id).first()

    dojo_filters = []
    dojo_filters.append(dojo.challenges_query())

    if not dojo.public:
        members = db.session.query(DojoMembers.user_id).filter_by(dojo_id=dojo_id)
        dojo_filters.append(Solves.account_id.in_(members.subquery()))

    standings_query = (
        db.session.query(*fields)
        .join(Challenges)
        .join(Model, Model.id == Solves.account_id)
        .filter(Challenges.value != 0, Model.banned == False, Model.hidden == False,
                *dojo_filters)
    )

    return standings_query


def belt_challenges():
    yellow_categories = [
        "embryoio",
        "babysuid",
        "embryoasm",
        "babyshell",
        "babyjail",
        "embryogdb",
        "babyrev",
        "babymem",
        "toddlerone",
    ]

    blue_categories = [
        *yellow_categories,
        "babyrop",
        "babyheap",
        "babyrace",
        "babykernel",
        "toddlertwo",
    ]

    color_categories = {
        "yellow": yellow_categories,
        "blue": blue_categories,
    }

    return {
        color: db.session.query(Challenges.id).filter(
            Challenges.state == "visible",
            Challenges.value > 0,
            Challenges.category.in_(categories),
        )
        for color, categories in color_categories.items()
    }
