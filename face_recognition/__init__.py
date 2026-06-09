"""Small dlib-backed subset of the face_recognition API used by this app.

This avoids installing the PyPI face_recognition package on Render because that
package depends on source-built dlib. The app installs dlib-bin instead.
"""

import numpy as np

import dlib
import face_recognition_models


_face_detector = dlib.get_frontal_face_detector()
_pose_predictor_68 = dlib.shape_predictor(face_recognition_models.pose_predictor_model_location())
_pose_predictor_5 = dlib.shape_predictor(face_recognition_models.pose_predictor_five_point_model_location())
_face_encoder = dlib.face_recognition_model_v1(face_recognition_models.face_recognition_model_location())


def _rect_to_css(rect):
    return rect.top(), rect.right(), rect.bottom(), rect.left()


def _css_to_rect(css):
    top, right, bottom, left = css
    return dlib.rectangle(int(left), int(top), int(right), int(bottom))


def _raw_face_locations(face_image, number_of_times_to_upsample=1):
    return _face_detector(face_image, number_of_times_to_upsample)


def face_locations(face_image, number_of_times_to_upsample=1, model="hog"):
    del model
    return [_rect_to_css(rect) for rect in _raw_face_locations(face_image, number_of_times_to_upsample)]


def _raw_face_landmarks(face_image, face_locations=None, model="large"):
    predictor = _pose_predictor_68 if model == "large" else _pose_predictor_5
    if face_locations is None:
        face_locations = _raw_face_locations(face_image)
    else:
        face_locations = [_css_to_rect(location) for location in face_locations]

    return [predictor(face_image, face_location) for face_location in face_locations]


def face_encodings(face_image, known_face_locations=None, num_jitters=1, model="small"):
    raw_landmarks = _raw_face_landmarks(face_image, known_face_locations, model)
    return [
        np.array(_face_encoder.compute_face_descriptor(face_image, landmark_set, num_jitters))
        for landmark_set in raw_landmarks
    ]


def face_distance(face_encodings, face_to_compare):
    if len(face_encodings) == 0:
        return np.empty((0,))
    face_encodings = np.asarray(face_encodings, dtype=np.float64)
    face_to_compare = np.asarray(face_to_compare, dtype=np.float64)
    return np.linalg.norm(face_encodings - face_to_compare, axis=1)


def _shape_points(shape, indexes):
    return [(shape.part(index).x, shape.part(index).y) for index in indexes]


def face_landmarks(face_image, face_locations=None, model="large"):
    raw_landmarks = _raw_face_landmarks(face_image, face_locations, model="large")
    landmarks = []

    for shape in raw_landmarks:
        landmarks.append(
            {
                "chin": _shape_points(shape, range(0, 17)),
                "left_eyebrow": _shape_points(shape, range(17, 22)),
                "right_eyebrow": _shape_points(shape, range(22, 27)),
                "nose_bridge": _shape_points(shape, range(27, 31)),
                "nose_tip": _shape_points(shape, range(31, 36)),
                "left_eye": _shape_points(shape, range(36, 42)),
                "right_eye": _shape_points(shape, range(42, 48)),
                "top_lip": _shape_points(shape, [48, 49, 50, 51, 52, 53, 54, 64, 63, 62, 61, 60]),
                "bottom_lip": _shape_points(shape, [54, 55, 56, 57, 58, 59, 48, 60, 67, 66, 65, 64]),
            }
        )

    return landmarks
