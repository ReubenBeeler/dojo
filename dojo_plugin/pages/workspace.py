from flask import request, Blueprint, Response, render_template
from CTFd.utils.user import get_current_user
from CTFd.utils.decorators import authed_only

from ..utils import get_current_challenge_id, random_home_path


workspace = Blueprint("pwncollege_workspace", __name__)


@workspace.route("/workspace")
@workspace.route("/<dojo>/workspace")
@authed_only
def view_workspace(dojo=None):
    active = get_current_challenge_id() is not None
    return render_template("workspace.html", dojo=dojo, active=active)


@workspace.route("/workspace/")
@workspace.route("/workspace/<path:path>")
@authed_only
def forward_workspace(dojo, path=""):
    prefix = "/workspace/"
    assert request.full_path.startswith(prefix)
    path = request.removeprefix(prefix)

    response = Response()

    user = get_current_user()
    redirect_uri = f"http://unix:/var/homes/nosuid/{random_home_path(user)}/.local/share/code-server/workspace.socket:/{path}"

    response.headers["X-Accel-Redirect"] = "/internal/"
    response.headers["redirect_uri"] = redirect_uri

    return response
