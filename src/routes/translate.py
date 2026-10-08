from flask import Blueprint, jsonify, request

from src.translator import TranslationError, translate

translate_bp = Blueprint('translate', __name__)


@translate_bp.route('/translate', methods=['POST'])
def translate_note():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error='Request body must be a JSON object.', code='invalid_input'), 400
    try:
        return jsonify(translate(data.get('text'), data.get('target_language')))
    except TranslationError as error:
        return jsonify(error=str(error), code=error.code), error.status
