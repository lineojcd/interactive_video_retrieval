"""
This is the web server
"""


import numpy as np
import cv2
import re
from PIL import Image
import base64
import io
import json

import os
from typing import List
from random import sample

from flask import Flask, render_template, request, jsonify, make_response, send_file, url_for

import requests

from src.database import Entry, db, Base, session
from src.config import CONFIG
from src.hdf5_manager import hdf5_file

from src.spatial_histogram import histogram_comparator
from src.object_recognition import labels
from src.sentence_embedding import find_similarity
import json

with open("static/all_labels.json", "w") as f:
    print(labels)
    json.dump(labels, f)

n_bins = CONFIG['n_hist_bins']
n_cols = CONFIG['n_hist_cols']
n_rows = CONFIG['n_hist_rows']


app = Flask(__name__)
app.debug = True
app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///data/database.db"
hdf5_file.set_path("data/features.hdf5")
db.init_app(app)

_entries =  session.query(Entry).all()
_entries_dict = dict()
for e in _entries:
    _entries_dict[e.histogram_feature_index] = e
_captions = [e.caption for e in _entries]

_current_bookmarks = []


def subquery(entities, sub):
    if sub is None or len(sub) == 0:
        return entities
    sub_ids = [int(t['id']) for t in sub]
    return [e for e in entities if e.id in sub_ids]


def perform_query(string, k, sub = None):
    # TODO implemented the actual query to perform
    tokens = string.split(",")
    tokens = [t.strip().lower() for t in tokens]
    res = []

    imgs = db.session.query(Entry).all() #type:List[Entry]
    imgs = subquery(imgs, sub)

    for i in imgs:
        labels = i.get_query_strings()
        all_found = []
        for string in tokens:
            for k in labels:
                if string in k:
                    all_found.append(string)
                    break

        if all_found == tokens:
            res.append(i)
    return res


@app.route('/')
def index():
    return render_template("index.html")


@app.route('/screenshot/<string:file_path>')
def get_screenshot(file_path):
    file_path = file_path.replace("|", "/")
    path = os.path.abspath(file_path)
    return send_file(path, mimetype='image/gif')


"http://<server address>/submit?item=<video id>&frame=<frame number>&shot=<shot id>&timecode=<time code>&session=<session id>"
@app.route('/submit/<string:video>/<int:frame>')
@app.route('/submit/<string:video>/<int:frame>/')
def submit(video, frame):
    """
    Submits a given result to the VBS Server
    :param video: The name of the movie
    :param frame: The frame position of the result
    :return:
    """
    url = CONFIG['server'] \
          + "?" \
          + "&item=" + video \
          + "&frame=" + str(frame) \
          + "&session=" + CONFIG['member']
    print("Submit to:", url)

    requests.get(url)
    return make_response("Submitted", 200)


@app.route("/query/", methods=["POST"])
def query():
    """
    Performs a query and returns a list of results to the front end.
    :return:
    """
    q = request.json['query']
    sub = request.json['subquery']
    embedding = request.json['embedding']

    if len(sub) == 0:
        sub = None

    if embedding:
        if sub is None:
            best_matches, _ = find_similarity(q, _captions, _entries, number_top_matches=1000)
            res = subquery(best_matches, sub)
        else:
            best_matches, _ = find_similarity(q, _captions, _entries, number_top_matches=50000)
            res = subquery(best_matches, sub)
    else:
        res = perform_query(q, 10, sub=sub)

    print(res)
    res = [r.to_json() for r in res]
    return jsonify(res)


@app.route("/similar/", methods=["POST"])
def similar():
    """
    Performs a query and returns a list of results to the front end.
    :return:
    """
    q = request.json['query']
    # print(q)
    e = db.session.query(Entry).filter(Entry.id == q['id']).one_or_none()
    if e is None:
        return make_response("not found", 404)
    else:
        img = cv2.imread(e.thumbnail_path)
        img_lab = cv2.cvtColor(img.astype(np.float32) / 255, cv2.COLOR_BGR2LAB)

        indices, distances = hdf5_file.fit(img_lab, "histograms", func=histogram_comparator)
        result = []
        for idx in indices:
            r = db.session.query(Entry).filter(Entry.histogram_feature_index == int(idx)).one_or_none()
            if r is not None:
                result.append(r)
        # result = subquery(result, sub)

        results = [r.to_json() for r in result]
        return jsonify(results)

    # if len(sub) == 0:
    #     sub = None

    # return jsonify(perform_query(q, 10, sub=sub))

@app.route("/query-image/", methods=["POST"])
def query_image():
    """
    Performs a query and returns a list of results to the front end.
    :return:
    """


    data = request.values['imageBase64']
    data = re.sub('^data:image/.+;base64,', '', data)
    data = base64.b64decode(data)
    sub = json.loads(request.values['subquery'])


    nparr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    img_lab = cv2.cvtColor(img.astype(np.float32) / 255, cv2.COLOR_BGR2LAB)
    alpha = np.zeros(shape = (img.shape[:2]), dtype=np.float32)
    alpha[np.where(np.sum(img, axis=2) > 0)] = 1.0
    img_lab = np.dstack((img_lab, alpha))

    if sub is None:
        indices, distances = hdf5_file.fit(img_lab, "histograms", func=histogram_comparator)

        results = []
        for idx in indices:
            results.append(_entries_dict[idx])
    else:
        indices, distances = hdf5_file.fit(img_lab, "histograms", func=histogram_comparator, k=50000)
        results = []
        for idx in indices:
            results.append(_entries_dict[idx])
        results = subquery(results, sub)

    results = results[:2000]
    results = [r.to_json() for r in results]
    print("Done", results)
    return jsonify(results)


@app.route("/update-bookmarks/", methods=["POST"])
def update_bookmarks():
    books = request.json['bookmarks']
    global _current_bookmarks
    _current_bookmarks = db.session.query(Entry).filter(Entry.id.in_(books)).all()
    return make_response("OK")


@app.route("/get-bookmarks/")
def get_bookmarks():
    return jsonify([e.to_json() for e in _current_bookmarks])
    pass

@app.route('/get-movie-clips/', methods=["POST"])
def get_all_of_movie():
    q = request.json['query']
    print(q)
    entries = db.session.query(Entry).filter(Entry.movie_name == q['location']['movie']).all()
    print(entries)
    results = [r.to_json() for r in entries]
    print("Done", results)
    return jsonify(results)


if __name__ == '__main__':
    app.run()

