#!/usr/bin/python

import util

NAME = 'vttablet'

class Vttablet(util.BaseCollector):
    def __init__(self, collectd, json_provider=None, verbose=False, interval=None):
        super(Vttablet, self).__init__(collectd, NAME, 15101, json_provider, verbose, interval)
        self.include_per_table_per_user_stats = True
        self.include_per_user_timings = True
        self.include_streamlog_stats = True
        self.include_acl_stats = True
        self.include_results_histogram = True
        self.include_reparent_timings = True
        self.include_heartbeat = False
        self.include_query_timings = False
        self.include_per_table_stats = True
        self.include_vtickets_stats = False

    def configure_callback(self, conf):
        super(Vttablet, self).configure_callback(conf)
        for node in conf.children:
            if node.key == 'IncludeResultsHistogram':
                self.include_results_histogram = util.boolval(node.values[0])
            elif node.key == 'IncludeStatsPerTablePerUser':
                self.include_per_table_per_user_stats = util.boolval(node.values[0])
            elif node.key == 'IncludeTimingsPerUser':
                self.include_per_user_timings = util.boolval(node.values[0])
            elif node.key == 'IncludeStreamLog':
                self.include_streamlog_stats = util.boolval(node.values[0])
            elif node.key == 'IncludeACLStats':
                self.include_acl_stats = util.boolval(node.values[0])
            elif node.key == 'IncludeExternalReparentTimings':
                self.include_reparent_timings = util.boolval(node.values[0])
            elif node.key == 'IncludeHeartbeat':
                self.include_heartbeat = util.boolval(node.values[0])
            elif node.key == 'IncludeQueryTimings':
                self.include_query_timings = util.boolval(node.values[0])
            elif node.key == 'IncludePerTableStats':
                self.include_per_table_stats = util.boolval(node.values[0])
            elif node.key == 'IncludeVTicketsStats':
                self.include_vtickets_stats = util.boolval(node.values[0])

        self.register_read_callback()

    def process_data(self, json_data):
        # Current connections and total accepted
        self.process_metric(json_data, 'ConnAccepted', 'counter')
        self.process_metric(json_data, 'ConnCount', 'gauge')

        # Health-related metrics.
        # TabletState is an integer mapping to one of SERVING (2), NOT_SERVING (0, 1, 3), or SHUTTING_DOWN (4)
        self.process_metric(json_data, 'TabletState', 'gauge', base_tags={'TabletType': json_data['TabletType'].lower()},)
        # Report on whether this is a master
        self.process_metric(json_data, 'TabletType', 'gauge', alt_name='IsMaster', transformer=lambda val: 1 if val.lower() == 'master' else 0)
        self.process_metric(json_data, 'HealthcheckErrors', 'counter', parse_tags=['keyspace', 'shard', 'type'])

        # GC Stats
        memstats = json_data['memstats']
        self.process_metric(memstats, 'GCCPUFraction', 'counter', prefix='GC.', alt_name='CPUFraction')
        self.process_metric(memstats, 'PauseTotalNs', 'counter', prefix='GC.')

        # Tracking usage of the connection pools used by apps
        self.process_pool_data(json_data, 'Conn')
        self.process_pool_data(json_data, 'StreamConn')
        self.process_pool_data(json_data, 'Transaction')
        self.process_pool_data(json_data, 'FoundRows')

        # Tracking ExecuteOptions_DBA transactions
        self.process_metric(json_data, 'TransactionPoolDbaInUse', 'gauge')
        self.process_metric(json_data, 'TransactionPoolDbaTotal', 'gauge')

        # If enabled, track histogram of number of results returned from user queries
        if self.include_results_histogram:
            self.process_histogram(json_data, 'Results')

        # Counters tagged by type, for tracking various error modes of the vttablet
        for metric in ['Errors', 'InternalErrors', 'Kills']:
            self.process_metric(json_data, metric, 'counter', parse_tags=['type'])

        if self.include_per_table_stats:
          # Counters tagged by table and type, for tracking counts of the various query types, times, and ways in which a query can fail
          # all broken down by table
          for metric in ['QueryCounts', 'QueryErrorCounts', 'QueryRowCounts', 'QueryTimesNs']:
              alt_name = 'QueryTimes' if metric == 'QueryTimeNs' else None
              transformer = util.nsToMs if metric == 'QueryTimesNs' else None
              self.process_metric(json_data, metric, 'counter', alt_name=alt_name, parse_tags=['table', 'type'], transformer=transformer)

          # Tracks data from information_schema about the size of tables
          for metric in ['DataFree', 'DataLength', 'IndexLength', 'TableRows']:
              self.process_metric(json_data, metric, 'gauge', parse_tags=['table'])

        if self.include_per_table_per_user_stats:
            # Tracks counts and timings of user queries by user, table, and type
            user_table_tags = ['table', 'user', 'type']
            self.process_metric(json_data, 'UserTableQueryCount', 'counter', parse_tags=user_table_tags)
            self.process_metric(json_data, 'UserTableQueryTimesNs', 'counter', alt_name='UserTableQueryTime', parse_tags=user_table_tags, transformer=util.nsToMs)

            # Tracks counts and timings of user transactions by user and type
            user_tx_tags = ['user', 'type']
            self.process_metric(json_data, 'UserTransactionCount', 'counter', parse_tags=user_tx_tags)
            self.process_metric(json_data, 'UserTransactionTimesNs', 'counter', alt_name='UserTransactionTime', parse_tags=user_tx_tags, transformer=util.nsToMs)

        # Tracks a variety of metrics for timing of the various layers of execution
        # MySQL is how long it takes to actually execute in MySQL. While Queries is the total time with vitess overhead
        # Waits tracks instances where we are able to consolidate identical queries while waiting for a connection
        self.process_timing_data(json_data, 'Mysql', process_histograms=False)
        self.process_timing_data(json_data, 'Queries', process_histograms=False)
        self.process_timing_data(json_data, 'Transactions', process_histograms=False)
        self.process_timing_data(json_data, 'Waits')
        if self.include_reparent_timings:
            self.process_timing_data(json_data, 'ExternalReparents', process_histograms=False)

        # MySQL timings above, broken down by user
        if self.include_per_user_timings:
            self.process_timing_data(json_data, 'MysqlAllPrivs')
            self.process_timing_data(json_data, 'MysqlApp')
            self.process_timing_data(json_data, 'MysqlDba')

        # Track usage of Vitess' query PLAN cache
        self.process_metric(json_data, 'QueryCacheCapacity', 'gauge', alt_name='QueryPlanCacheCapacity')
        self.process_metric(json_data, 'QueryCacheLength', 'gauge', alt_name='QueryPlanCacheLength')

        # Tracks messages sent and success of delivery for the stream log
        if self.include_streamlog_stats:
            self.process_metric(json_data, 'StreamlogSend', 'counter', parse_tags=['log'])
            parse_tags = ['log', 'subscriber']
            self.process_metric(json_data, 'StreamlogDelivered', 'counter', parse_tags=parse_tags)
            self.process_metric(json_data, 'StreamlogDeliveryDroppedMessages', 'counter', parse_tags=parse_tags)

        # Tracks the impact of ACLs on user queries
        if self.include_acl_stats and self.include_per_table_stats:
            acl_tags = ['table', 'plan', 'id', 'user']
            self.process_metric(json_data, 'TableACLAllowed', 'counter', parse_tags=acl_tags)
            self.process_metric(json_data, 'TableACLDenied', 'counter', parse_tags=acl_tags)
            self.process_metric(json_data, 'TableACLPseudoDenied', 'counter', parse_tags=acl_tags)
            # Super users are exempt and are tracked by this
            self.process_metric(json_data, 'TableACLExemptCount', 'counter')
            # Look for DDL executed by users not in migration group
            for tags, value in self._extract_values(json_data, 'TableACLAllowed', acl_tags):
                if tags['id'] == "DDL" and not tags['user'].startswith('migration.'):
                    self.emitter.emit("UnprivilegedDDL", value, 'counter', tags)

        if self.include_heartbeat:
            self.process_metric(json_data, 'HeartbeatCurrentLagNs', 'gauge')
            self.process_metric(json_data, 'HeartbeatReads', 'counter')
            self.process_metric(json_data, 'HeartbeatReadErrors', 'counter')
            self.process_metric(json_data, 'HeartbeatWrites', 'counter')
            self.process_metric(json_data, 'HeartbeatWriteErrors', 'counter')


        if self.include_query_timings:
            query_timing_tags = ['Median', 'NinetyNinth']
            if "AggregateQueryTimings" in json_data:
                timing_json = json_data["AggregateQueryTimings"]
                self.process_timing_quartile_metric(timing_json, "TotalQueryTime")
                self.process_timing_quartile_metric(timing_json, "MysqlQueryTime")
                self.process_timing_quartile_metric(timing_json, "ConnectionAcquisitionTime")

        if self.include_vtickets_stats:
            table_tag = ['table']
            self.process_metric(json_data, 'VTicketsUsed', 'counter', parse_tags=table_tag)
            self.process_metric(json_data, 'VTicketsRemaining', 'gauge', parse_tags=table_tag)
            self.process_metric(json_data, 'VTicketsRefills', 'counter', parse_tags=table_tag)
            self.process_metric(json_data, 'VTicketsFailedRefills', 'counter', parse_tags=table_tag)
            self.process_metric(json_data, 'VTicketsBlockingRefills', 'counter', parse_tags=table_tag)
            self.process_metric(json_data, 'VTicketsBatchSize', 'gauge', parse_tags=table_tag)
            query_timing_tags = ['Median', 'NinetyNinth']
            if "VTicketsServiceCallsTimings" in json_data:
                timing_json = json_data["VTicketsServiceCallsTimings"]
                self.process_timing_quartile_metric(timing_json, "VTicketsFetchTime")

    def process_pool_data(self, json_data, pool_name):
        self.process_metric(json_data, '%sPoolAvailable' % pool_name, 'gauge')
        self.process_metric(json_data, '%sPoolCapacity' % pool_name, 'gauge')
        self.process_metric(json_data, '%sPoolWaitCount' % pool_name, 'counter')
        self.process_metric(json_data, '%sPoolWaitTime' % pool_name, 'counter', transformer=util.nsToMs)
        self.process_metric(json_data, '%sPoolIdleClosed' % pool_name, 'counter')
        self.process_metric(json_data, '%sPoolExhausted' % pool_name, 'counter')

if __name__ == '__main__':
    util.run_local(NAME, Vttablet)
else:
    import collectd
    vt = Vttablet(collectd)
    collectd.register_config(vt.configure_callback)
