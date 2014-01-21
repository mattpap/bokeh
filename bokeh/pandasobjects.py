import pandas
import time
import protocol
import numpy as np
import requests

import logging
logger = logging.getLogger(__name__)

from bokeh.properties import (HasProps, MetaHasProps,
        Any, Dict, Enum, Float, Instance, Int, List, String,
        Color, Pattern, Percent, Size, Bool)

#loading dependencies
import bokeh.objects
import bokeh.glyphs

from bokeh.objects import PlotObject, Plot, ColumnDataSource
from bokeh.session import PlotContext, PlotList

# Hugo: this object model is still a bit half baked
# we are probabyl storing some things on the plot source and
# pivot table that we should store on the IPythonRemoteData

class IPythonRemoteData(PlotObject):
    host  = String("localhost")
    port = Int(10020)
    varname = String()
    computed_columns = List()
    metadata = Dict()

    #hack... we're just using this field right now to trigger events
    selected = Int(0)
    data = Int(0)

    # from IPython.kernel import KernelManager
    # kernel = KernelManager(connection_file="kernel-1.json")
    # kernel.load_connection_file()
    # client = kernel.client()
    # client.start_channels()
    # client.shell_channel.execute("x = 1", store_history=False)

    def _url(self, func=None):
        remotedata = self
        func = "/" + func if func is not None else ""
        url = "http://%s:%s/array/%s%s" % \
            (remotedata.host, remotedata.port, remotedata.varname, func)
        return url

    def _trigger_events(self):
        self.selected = self.selected + 1

    def setselect(self, select, transform):
        data = transform
        data['selected'] = select
        requests.post(self._url("setselect"), data=protocol.serialize_json(data))
        self._trigger_events()

    def search(self, search):
        requests.post(self._url("search"), data=search)
        self._trigger_events()

    def select(self, select, transform):
        data = transform
        data['selected'] = select
        requests.post(self._url("select"), data=protocol.serialize_json(data))
        self._trigger_events()

    def deselect(self, deselect, transform):
        data = transform
        data['selected'] = deselect
        requests.post(self._url("selected"), data=protocol.serialize_json(data))
        self._trigger_events()

    def pivot(self, transform):
        data = requests.post(self._url("pivot"), data=protocol.serialize_json(transform)).json()
        self._trigger_events()
        return data

    def get_data(self, transform):
        data = requests.get(self._url(), data=protocol.serialize_json(transform)).json()
        self.metadata = data.pop('metadata', {})
        return data

    def set_computed_columns(self, computed_columns):
        data = requests.get(self._url("computed"), data=protocol.serialize_json(computed_columns)).json()
        self.computed_columns = computed_columns
        self.data += 1
        return data

class PandasPlotSource(ColumnDataSource):
    source = Instance(has_ref=True)

    def __init__(self, *args, **kwargs):
        super(PandasPlotSource, self).__init__(*args, **kwargs)

    def setup_events(self):
        self.on_change('selected', self, 'selection_callback')
        self.source.on_change('selected', self, 'get_data')
        self.source.on_change('data', self, 'get_data')
        self.source.on_change('computed_columns', self, 'get_data')
        if not self.data:
            self.get_data()

    def selection_callback(self, obj=None, attrname=None, old=None, new=None):
        self.setselect(self.selected)

    def transform(self):
        return {}

    def setselect(self, select):
        self.source.setselect(select, self.transform())
        self.get_data()

    def select(self, select):
        self.source.select(select, self.transform())
        self.get_data()

    def deselect(self, deselect):
        self.source.deselect(deselect, self.transform())
        self.get_data()

    def get_data(self, obj=None, attrname=None, old=None, new=None):
        data = self.source.get_data(self.transform())
        #ugly:
        self._selected =  np.nonzero(data['data']['_selected'])[0]
        self.maxlength = data.pop('maxlength')
        self.totallength = data.pop('totallength')
        self.column_names = data['column_names']
        self.data = data['data']

class PivotTable(PlotObject):
    source = Instance(has_ref=True)
    attrs = List()                       # List[String]
    data = Dict()                        # Dict[String, Object]
    rows = List()                        # List[String]
    cols = List()                        # List[String]
    vals = String()
    renderer = Enum("table", "table-barchart", "heatmap", "row-heatmap", "col-heatmap")
    aggregator = Enum("count", "sum", "average")
    description = String()

    def setup_events(self):
        self.on_change('attrs', self, 'get_data')
        self.on_change('rows', self, 'get_data')
        self.on_change('cols', self, 'get_data')
        self.on_change('vals', self, 'get_data')
        self.on_change('renderer', self, 'get_data')
        self.on_change('aggregator', self, 'get_data')

        if not self.data:
            self.get_data()

    def get_data(self, obj=None, attrname=None, old=None, new=None):
        logger.info("PivotTable.get_data()")
        self.data = self.source.pivot(dict(
            rows=self.rows,
            cols=self.cols,
            vals=self.vals,
            aggregator=self.aggregator,
        ))

class PandasPivotTable(PlotObject):
    source = Instance(has_ref=True)
    columns = List()
    sort = List()
    group = List()
    offset = Int(default=0)
    length = Int(default=100)
    maxlength = Int()
    totallength = Int()
    precision = Dict()
    tabledata = Dict()
    filterselected = Bool(default=False)

    def setup_events(self):
        self.on_change('columns', self, 'get_data')
        self.on_change('sort', self, 'get_data')
        self.on_change('group', self, 'get_data')
        self.on_change('length', self, 'get_data')
        self.on_change('offset', self, 'get_data')
        self.on_change('precision', self, 'get_data')
        self.on_change('filterselected', self, 'get_data')
        self.source.on_change('selected', self, 'get_data')
        self.source.on_change('data', self, 'get_data')
        self.source.on_change('computed_columns', self, 'get_data')
        if not self.tabledata:
            self.get_data()

    def format_data(self, jsondata):
        """inplace manipulation of jsondata
        """
        precision = self.precision
        for colname, data in jsondata.iteritems():
            if colname == '_selected' or colname == '_counts':
                continue
            if self.source.metadata.get(colname, {}).get('date'):
                isdate = True
            else:
                isdate = False
            for idx, val in enumerate(data):
                if isdate:
                    timeobj = time.localtime(val/1000.0)
                    data[idx] = time.strftime("%Y-%m-%d %H:%M:%S", timeobj)
                if isinstance(val, float):
                    data[idx] = "%%.%df" % precision.get(colname,2)%data[idx]

    def transform(self):
        return dict(columns=self.columns,
                    sort=self.sort,
                    group=self.group,
                    offset=self.offset,
                    length=self.length,
                    filterselected=self.filterselected)

    def setselect(self, select):
        self.source.setselect(select, self.transform())
        self.get_data()

    def select(self, select):
        self.source.select(select, self.transform())
        self.get_data()

    def deselect(self, deselect):
        self.source.deselect(deselect, self.transform())
        self.get_data()

    def get_data(self, obj=None, attrname=None, old=None, new=None):
        data = self.source.get_data(self.transform())
        #print data['data']['_selected']
        self.maxlength = data.pop('maxlength')
        self.totallength = data.pop('totallength')
        self.format_data(data['data'])
        self.tabledata = data

