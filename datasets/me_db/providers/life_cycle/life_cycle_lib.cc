#include "datasets/me_db/providers/life_cycle/life_cycle_lib.h"

#include "datasets/me_db/me.pb.h"

#include "phd/macros.h"
#include "phd/string.h"

#include "absl/strings/str_cat.h"
#include "absl/strings/str_split.h"
#include "absl/time/time.h"
#include "absl/container/flat_hash_map.h"

#include <boost/filesystem.hpp>
#include <boost/filesystem/fstream.hpp>

namespace me {

// The number of milliseconds in a day.
constexpr int64_t MILLISECONDS_IN_DAY =
    /*second=*/1000 * /*hour=*/3600 * /*day=*/24;


int64_t RoundToStartOfNextDay(const int64_t ms_since_unix_epoch) {
  // Divide by milliseconds in day to produce the number of days elapsed since
  // epoch. Since this is integer division, this rounds down.
  const int64_t days_since_epoch_utc =
      ms_since_unix_epoch / MILLISECONDS_IN_DAY;

  // Add one to day count and multiply back to milliseconds.
  return (days_since_epoch_utc + 1) * MILLISECONDS_IN_DAY;
}


absl::Time ParseLifeCycleDatetimeOrDie(const string& date) {
  absl::Time time;
  std::string err;
  bool succeeded = absl::ParseTime("%Y-%m-%d %H:%M:%S", date, &time, &err);
  if (!succeeded) {
    FATAL("Failed to parse '%s': %s", date, err);
  }
  return time;
}


int64_t ToMillisecondsSinceUnixEpoch(const absl::Time& time) {
  absl::Duration d = time - absl::UnixEpoch();
  return d / absl::Milliseconds(1);
}


void AddMeasurementsFromDurationOrDie(int64_t start_time, int64_t end_time,
                                      const string& location, Series* series) {
  int64_t remaining_time_to_allocate = end_time - start_time;
  int64_t end_of_day = RoundToStartOfNextDay(start_time);

  while (remaining_time_to_allocate > 0) {
    int64_t duration = std::min(
        remaining_time_to_allocate, end_of_day - start_time);

    // Create the new measurement.
    Measurement* measurement = series->add_measurement();
    measurement->set_ms_since_unix_epoch(start_time);
    measurement->set_value(duration);
    measurement->set_group(location);
    measurement->set_source("LifeCycle");

    start_time = end_of_day;
    remaining_time_to_allocate -= MILLISECONDS_IN_DAY;
    end_of_day += MILLISECONDS_IN_DAY;
  }
}


string LocationToGroup(const string& location) {
  const string location_stripped = phd::CopyAndTrimLeft(location);
  if (location_stripped == "") {
    return "default";
  } else {
    return phd::ToCamelCase(location_stripped);
  }
}


void ProcessLineOrDie(
    const std::string& line, const int64_t line_num,
    const boost::filesystem::path csv_path, SeriesCollection* const proto,
    absl::flat_hash_map<string, Series*>* const name_to_series_map) {
  // Split the comma separated line.
  std::vector<absl::string_view> components = absl::StrSplit(line, ',');
  if (components.size() < 8) {
    FATAL("Line %d of `%s` does not have 8 columns: '%s'",
          line_num, csv_path.string(), line);
  }

  // Split out and parse the components from the row.

  // [0] START DATE(UTC)              Datetime.
  const int64_t start_date = ToMillisecondsSinceUnixEpoch(
      ParseLifeCycleDatetimeOrDie(string(components[0])));
  // [1] END DATE(UTC)                Datetime.
  const int64_t end_date = ToMillisecondsSinceUnixEpoch(
      ParseLifeCycleDatetimeOrDie(string(components[1])));
  // [unused] [2] START TIME(LOCAL)   Datetime.
  // [unused] [3] END TIME(LOCAL)     Datetime.
  // [unused] [4] DURATION            (end-start) in seconds.
  // [5] NAME (optional)              Category name.
  const string name = phd::CopyAndTrimLeft(string(components[5]));
  // [6] LOCATION (optional)          Location name.
  const string location = LocationToGroup(string(components[6]));
  // [unused] [7] NOTE (optional)     Notes field.

  // Find the series that the measurements should belong to. If the Series
  // does not exist, create it.
  Series* series = FindOrAdd<string, Series*>(
      name_to_series_map, name,
      [name_to_series_map,proto](const string& name) -> Series* {
    Series* series = proto->add_series();
    series->set_name(absl::StrCat(phd::ToCamelCase(name), "Time"));
    series->set_family("TimeTracking");
    series->set_unit("milliseconds");
    name_to_series_map->insert(
        std::make_pair(name, series));
    return series;
  });

  AddMeasurementsFromDurationOrDie(start_date, end_date, location, series);
}

void ProcessSeriesCollectionOrDie(SeriesCollection* proto) {
  const boost::filesystem::path csv_path(proto->source());

  CHECK(boost::filesystem::is_regular_file(csv_path));
  INFO("Reading from CSV file %s", csv_path.string());

  boost::filesystem::ifstream csv(csv_path);
  CHECK(csv.is_open());

  string line;
  // Process the first line of the header.
  std::getline(csv, line);
  if (line != ("START DATE(UTC), END DATE(UTC), START TIME(LOCAL), "
               "END TIME(LOCAL), DURATION, NAME, LOCATION, NOTE")) {
    FATAL("Expected first line of `%s` to contain column names. Actual "
          "value: `%s`.", csv_path.string(), line);
  }

  // Process the second line of the header.
  std::getline(csv, line);
  if (line == "\n") {
    FATAL("Expected second line of `%s` to be empty. Actual value: `%s`",
          csv_path.string(), line);
  }

  // Keep a map from name columns to series. Measurements are assigned to named
  // Series. We use this map to determine which Series to add each Measurement
  // to.
  absl::flat_hash_map<string, Series*> name_to_series_map;

  // Iterate through the file.
  int line_num = 2;  // We skipped the first two lines.
  while (std::getline(csv, line)) {
    ++line_num;
    ProcessLineOrDie(line, line_num, csv_path, proto, &name_to_series_map);
  }
}

}  // namespace me
