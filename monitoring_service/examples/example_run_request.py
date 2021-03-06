import json
import time
import datetime
import requests
import numpy as np
import pandas as pd


_integer_types = (np.int_, np.intc, np.intp, np.int8, np.int16, np.int32, np.int64, np.uint8, np.uint16, np.uint32, np.uint64)
_float_types = (np.float_, np.float16, np.float32, np.float64)


class NumpyEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, _integer_types):
            return int(o)
        if isinstance(o, (np.float_, np.float16, np.float32, np.float64)):
            return float(o)
        if isinstance(o, (np.ndarray,)):
            return o.tolist()
        if isinstance(o, np.bool_):
            return bool(o)
        if isinstance(o, np.void):
            return None
        return json.JSONEncoder.default(self, o)


def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""

    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    raise TypeError("Type %s not serializable" % type(obj))


if __name__ == '__main__':
    new_data = pd.read_csv("data/production.csv")
    for idx in range(0, new_data.shape[0]):
        # to test request to service sending new data
        data = new_data.iloc[idx].to_dict()

        # first get the prediction output from the model API
        # use the URL from the bridge network, see:
        # https://docs.docker.com/network/network-tutorial-standalone/#use-the-default-bridge-network
        model_api_response = requests.post(
            'http://172.17.0.1:8000/predict',
            data=json.dumps(data, cls=NumpyEncoder, default=json_serial),
        )
        model_api_response_str = json.loads(model_api_response.text)

        # now send the model API's response to the drift monitoring service API
        requests.post('http://localhost:5000/metrics', json=json.loads(model_api_response_str))

        # pause a bit to simulate a non-constant data stream
        time.sleep(1)
