import os
import pandas as pd
from ruamel import yaml


DATA_DIR = "data/"


def create_config(feature_names, filename_suffix=None):
    """
    Create a new config file for the client_id in config/monitoring.

    :feature_names: (list of strings) names of the features, or the words for a BoW model
    :filename_suffix: (str) optional string to append to the config file name, such as when there
        is 1 config per model/client
    """
    # TODO: update this after seeing what prod will look like
    output = dict(
        data_format=dict(
            separator=",",
            header=True,
            date_column="date_",
        ),
        column_mapping=dict(
            target="target_",
            prediction="predicted_",
            datetime="date_",
            numerical_features=feature_names,
            categorical_features=[]
        ),
        pretty_print=True,
        service=dict(
            reference_path=f"{DATA_DIR}reference{filename_suffix or '_1'}.csv",
            min_reference_size=30,
            use_reference=True,
            moving_reference=False,
            window_size=30,
            calculation_period_sec=10,
            monitors=["data_drift", "concept_drift", "regression_performance"],
        ),
    )

    with open(f"config/monitoring/monitoring_config{filename_suffix or '_1'}.yaml", "w") as outfile:
        yaml.dump(output, outfile, default_flow_style=False)


class Monitor:
    def __init__(self, client_id: str, reference_data: pd.DataFrame=None):
        """

        :param client_id: the client ID/model ID
        :param reference_data: data up to the last training time.  This is the gold set and should
            not change during monitoring, until the model is re-trained.
        """
        self.client_id = client_id
        if reference_data is not None:
            self.reference_data = reference_data
        else:
            # get the reference data associated with the client ID
            # TODO: replace this with a DB call in prod
            reference_files = next(os.walk(DATA_DIR), (None, None, []))[2]
            reference_file = [f for f in reference_files if f"reference_{client_id}" in f].pop()
            self.reference_data = pd.read_csv(DATA_DIR + reference_file)
        # set up data frame to hold the data that will be compared to reference data
        self.current_data = pd.DataFrame(columns=self.reference_data.columns)
        # load the configuration options for this client_id
        try:
            with open(f"config/monitoring/monitoring_config_{self.client_id}.yaml", 'rb') as f:
                self.config = yaml.safe_load(f)
        except Exception as e:
            print(f"Error loading config for client {client_id}.  Does it exist?  Details:\n", e)


    def iterate(self, new_rows: pd.DataFrame):
        """

        :param new_rows: new metrics coming in from the model API
        :return:
        """
        # update current data with incoming rows
        self.current_data = self.current_data.append(new_rows, ignore_index=True)

        current_size = self.current_data.shape[0]
        if current_size < self.config['service']['window_size']:
            print("Not enough data for comparison.  Waiting for more requests...")
            return

        # drop the oldest samples by index to make current_data be a sliding window
        # note - if new_rows > window_size, you will lose some of the older new rows in the hypothesis tests
        self.current_data.drop(
            index=[x for x in range(0, current_size - self.config['service']['window_size'])],
            inplace=True
        )
        # reset the index, effectively shifting the oldest to the left
        self.current_data.reset_index(drop=True, inplace=True)

        # perform statistical analysis


m = Monitor(client_id="1", reference_data=None)
new_d = pd.read_csv("data/production.csv")
m.iterate(new_rows=new_d.iloc[:40])
m.iterate(new_rows=new_d.iloc[40:50])