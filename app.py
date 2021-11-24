import hashlib

import dataclasses
import datetime
from typing import Dict, List, Optional

import flask
import pandas
from evidently import model_monitoring
from evidently.model_monitoring import (
    DataDriftMonitor,
    ConceptDriftMonitor,
    RegressionPerformanceMonitor
)
from flask import Flask
from prometheus_client import Gauge
from prometheus_client import make_wsgi_app
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from evidently.runner.loader import DataLoader, DataOptions

app = Flask(__name__)

# Add prometheus wsgi middleware to route /metrics requests
app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {
    '/metrics': make_wsgi_app()
})


@dataclasses.dataclass
class MonitoringServiceOptions:
    reference_path: str
    min_reference_size: int
    use_reference: bool
    moving_reference: bool
    window_size: int
    calculation_period_sec: int
    monitors: List[str]


monitor_mapping = {
    "data_drift": DataDriftMonitor,
    "concept_drift": ConceptDriftMonitor,
    "regression_performance": RegressionPerformanceMonitor
}


class MonitoringService:
    metric: Dict[str, Gauge]
    last_run: Optional[datetime.datetime]

    def __init__(self,
                 reference: pandas.DataFrame,
                 options: MonitoringServiceOptions,
                 column_mapping: dict = None):
        """
        Sets up a monitoring service that handles things like calculating rolling windows.
        The service is initialized as global variable monitoring_service, and receives options specified in your
        config.yaml.  For example, if your window_size in config.yaml is 30, then 30 samples will be
        yanked out of the end of your reference dataset to seed the 'current' data, upon initialization.
        This ensures that there are enough samples to carry out statistical tests to compare distributions.
        As a side effect, these rows will pollute the current dataset that you want to compare.

        :param reference: Defined in config.yaml, points to the reference dataset (CSV)
        :param options: Defined in config.yaml
        :param column_mapping: Defined in config.yaml, determines which tests to apply based on var type
        """
        self.monitoring = model_monitoring.ModelMonitoring(monitors=[monitor_mapping[k] for k in options.monitors])

        if options.use_reference:
            self.reference = reference.iloc[:-options.window_size, :].copy()
            self.current = reference.iloc[-options.window_size:, :].copy()
        else:
            self.reference = reference.copy()
            self.current = pandas.DataFrame().reindex_like(reference).dropna()
        self.column_mapping = column_mapping
        self.options = options
        self.metrics = dict()
        # self.next_run_time = None
        self.new_rows = 0
        self.hash = hashlib.sha256(pandas.util.hash_pandas_object(self.reference).values).hexdigest()
        self.hash_metric = Gauge("evidently:reference_dataset_hash", "", labelnames=["hash"])

    def iterate(self, new_rows: pandas.DataFrame):
        """
        Takes in new data and appends it to the current dataset.  In the example (example_run_request.py),
        new samples are generated from the production.csv dataset 1 at a time.  They get passed into
        this function as new_rows.

        :param new_rows: New records to add to the current dataset.
        :return:
        """
        rows_count = new_rows.shape[0]

        self.current = self.current.append(new_rows, ignore_index=True)
        self.new_rows += rows_count
        current_size = self.current.shape[0]

        # TODO: replace this with better error handling
        if current_size < self.options.window_size:
            app.logger.info(f"Not enough data for measurement: {current_size} of {self.options.window_size}."
                            f" Waiting more data")
            return

        # if self.new_rows < self.options.window_size < current_size:
        # drop the oldest samples by index
        # note - if new_rows > window_size, you will lose some of the older new rows in the hypothesis tests
        self.current.drop(index=[x for x in range(0, current_size - self.options.window_size)], inplace=True)
        # reset the index, effectively shifting the oldest to the left
        self.current.reset_index(drop=True, inplace=True)
        # reduce new_rows count, now that they have been added to the data
        self.new_rows -= rows_count

        # if self.next_run_time is not None and self.next_run_time > datetime.datetime.now():
        #     app.logger.info(f"Next run at {self.next_run_time}")
        #     return
        # self.next_run_time = datetime.datetime.now() + datetime.timedelta(seconds=self.options.calculation_period_sec)

        self.monitoring.execute(self.reference, self.current, self.column_mapping)
        self.hash_metric.labels(hash=self.hash).set(1)
        for metric, value, labels in self.monitoring.metrics():
            metric_key = f"evidently:{metric.name}"
            found = self.metrics.get(metric_key)
            if not found:
                found = Gauge(metric_key, "", () if labels is None else list(sorted(labels.keys())))
                self.metrics[metric_key] = found
            if labels is None:
                found.set(value)
            else:
                found.labels(**labels).set(value)


monitoring_service: Optional[MonitoringService] = None


@app.before_first_request
def configure_service():
    """
    Initializes monitoring service with the configuration defined in config.yaml.
    The last 30 rows of the reference dataset become the current dataset used for comparison.
    New rows will be appended to this current dataset.
    """
    import yaml

    global monitoring_service
    with open("config.yaml", 'rb') as f:
        config = yaml.safe_load(f)
    loader = DataLoader()
    app.logger.info(f"config: {config}")
    options = MonitoringServiceOptions(**config['service'])

    reference_data = loader.load(options.reference_path,
                                 DataOptions(date_column=config['data_format']['date_column'],
                                             separator=config['data_format']['separator'],
                                             header=config['data_format']['header']))
    app.logger.info(f"reference dataset loaded: {len(reference_data)} rows")
    monitoring_service = MonitoringService(reference_data, options=options, column_mapping=config['column_mapping'])


@app.route('/iterate', methods=["POST"])
def iterate():
    """
    Routes incoming new data (flask.request.json) to the monitoring service to be appended to
    the current dataset (monitoring_service.iterate is called with new_rows argument).

    :return: 'ok' if the request is successful and new rows are appended to current
    """
    item = flask.request.json
    if monitoring_service is None:
        return 500, "Internal Server Error: service not found"
    monitoring_service.iterate(new_rows=pandas.DataFrame.from_dict(item))
    return "ok"


if __name__ == '__main__':
    app.run()



