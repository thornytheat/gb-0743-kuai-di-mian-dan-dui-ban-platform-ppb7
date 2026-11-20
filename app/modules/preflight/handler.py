from flask import Blueprint, jsonify

from app.web import payload, snapshot, store
from .service import listing, preflight

bp = Blueprint('preflight', __name__)


@bp.get('/api/preflight')
def inspect():
    run = snapshot()
    return jsonify(id=run['manifest']['id'], data=listing(store(), run['manifest']['id']))


@bp.post('/api/preflight')
def evaluate():
    return jsonify(preflight(store(), **payload('expected', 'at')))
