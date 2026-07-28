#!/usr/bin/env ruby
# system_load_tracker.rb
#
# Measures system-wide and per-user CPU/GPU load, power, and temperature.
#
# Modes:
#   --sample     Take a 1-second snapshot, append to a scratch file (run every minute)
#   --summarize  Average all samples from the current hour, append to the main CSV,
#                and clear the scratch file (run at the top of each hour)
#   --report     Generate HTML usage report and scp to remote server
#   --dry-run    Print a single snapshot without writing anything
#
# Usage:
#   ruby system_load_tracker.rb --sample    [--csv PATH] [--scratch PATH]
#   ruby system_load_tracker.rb --summarize [--csv PATH] [--scratch PATH]
#   ruby system_load_tracker.rb --dry-run
#
# Cron (run as root for full user visibility):
#   * * * * * /usr/bin/ruby /home/simon/system_load_tracker.rb --sample
#   1 * * * * /usr/bin/ruby /home/simon/system_load_tracker.rb --summarize
#
# Requirements:
#   - Linux (uses /proc for 1-second CPU snapshots)
#   - nvidia-smi (optional, for GPU metrics)
#   - sensors (optional, for CPU temperature via lm-sensors / k10temp)
#   - Ruby >= 2.5 (no gems needed)

require "csv"
require "json"
require "time"
require "optparse"
require "fileutils"
require "etc"

# ─── Configuration ───────────────────────────────────────────────────────────

DEFAULT_CSV     = File.join(__dir__, "system_load.csv")
DEFAULT_SCRATCH = File.join(__dir__, "system_load_samples.jsonl")
IGNORED_USERS   = %w[root nobody systemd-resolve systemd-timesync
                      syslog messagebus daemon avahi colord
                      rtkit pulse gdm gnome-initial-setup
                      systemd-network systemd-oom _apt
                      cups-pk-helper kernoops].freeze

# Idle power baselines (watts). Per-user watts are computed from power
# draw *above* these baselines, so idle draw isn't attributed to anyone.
# ganesha: dual-socket AMD Opteron 6274, CPU power measured via fam15h_power
# (AMD APM), NOT Intel RAPL. CPU_IDLE ~= both-socket idle package power (approx;
# tune from the observed minimum over time). No GPU on this box.
CPU_IDLE_WATTS = 90.0
GPU_IDLE_WATTS = 0.0

# Precision for numeric values in samples and CSV.
PRECISION = 4

# ─── Measurement helpers ─────────────────────────────────────────────────────

module Measure
  # Samples /proc/stat (system-wide) and /proc/[pid]/stat (per-process)
  # simultaneously over a 1-second window. Also reads RAPL if available.
  # Returns:
  #   [total_cpu_percent, { "username" => cpu_percent }, cpu_watts_or_nil]
  # Per-user values: 100% = one full core.
  def self.cpu_snapshot(sample_secs: 1)
    hz = `getconf CLK_TCK 2>/dev/null`.strip.to_f
    hz = 100.0 if hz.zero?

    # Read fam15h_power (AMD APM, µW) before/after alongside CPU state.
    p1 = fam15h_power_watts

    sys1, procs1 = sample_cpu_state
    sleep(sample_secs)
    sys2, procs2 = sample_cpu_state

    p2 = fam15h_power_watts

    # System-wide CPU %
    delta_total = sys2[:total] - sys1[:total]
    delta_idle  = sys2[:idle]  - sys1[:idle]
    total_pct = if delta_total.zero?
                  0.0
                else
                  (((delta_total - delta_idle) / delta_total) * 100).round(PRECISION)
                end

    # CPU power in watts from fam15h_power (running-average µW); avg the two reads.
    cpu_watts = if p1 && p2 then ((p1 + p2) / 2.0).round(PRECISION)
                elsif p2   then p2.round(PRECISION)
                elsif p1   then p1.round(PRECISION)
                else nil end

    # Per-process → per-user CPU %
    user_totals = Hash.new(0.0)
    elapsed = sample_secs.to_f

    procs2.each do |pid, t2|
      t1 = procs1[pid]
      next unless t1

      delta_ticks = (t2[:utime] + t2[:stime]) - (t1[:utime] + t1[:stime])
      pct = (delta_ticks.to_f / hz / elapsed) * 100.0

      user = t2[:user]
      next if user.nil? || IGNORED_USERS.include?(user)

      user_totals[user] += pct
    end

    user_totals.transform_values! { |v| v.round(PRECISION) }
    [total_pct, user_totals, cpu_watts]
  end

  def self.sample_cpu_state
    line = File.readlines("/proc/stat").find { |l| l.start_with?("cpu ") }
    fields = line.split[1..].map(&:to_f)
    sys = { idle: fields[3] + fields[4], total: fields.sum }

    procs = {}
    Dir.glob("/proc/[0-9]*/stat").each do |path|
      begin
        data = File.read(path)
        # Format: pid (comm) state ppid ... utime(14) stime(15) ...
        # Strip "pid (comm) state" then split the rest.
        next unless data =~ /\A(\d+) \(.*?\) \S+ (.+)/
        pid = $1.to_i
        rest = $2.split
        # rest[0] = ppid (field 4), so utime (field 14) = rest[10], stime (field 15) = rest[11]
        utime = rest[10].to_f
        stime = rest[11].to_f

        uid = File.read("/proc/#{pid}/status")[/^Uid:\s+(\d+)/, 1]
        user = uid ? (Etc.getpwuid(uid.to_i).name rescue nil) : nil

        procs[pid] = { utime: utime, stime: stime, user: user }
      rescue Errno::ENOENT, Errno::ESRCH, Errno::EACCES
        next
      end
    end

    [sys, procs]
  end

  def self.total_gpu
    return [nil, nil] unless command_exists?("nvidia-smi")

    csv = `nvidia-smi --query-gpu=utilization.gpu,utilization.memory \
           --format=csv,noheader,nounits 2>/dev/null`.strip
    return [nil, nil] if csv.empty?

    gpus = csv.lines.map { |l| l.split(",").map(&:strip).map(&:to_f) }
    avg_util = (gpus.sum { |g| g[0] } / gpus.size).round(PRECISION)
    avg_mem  = (gpus.sum { |g| g[1] } / gpus.size).round(PRECISION)
    [avg_util, avg_mem]
  end

  # Returns GPU power draw in watts (summed across all GPUs), or nil.
  def self.gpu_power_watts
    return nil unless command_exists?("nvidia-smi")

    csv = `nvidia-smi --query-gpu=power.draw \
           --format=csv,noheader,nounits 2>/dev/null`.strip
    return nil if csv.empty?

    csv.lines.map { |l| l.strip.to_f }.sum.round(PRECISION)
  end

  # Returns NVIDIA GPU temperature in °C (average across GPUs), or nil.
  def self.gpu_temp
    return nil unless command_exists?("nvidia-smi")

    csv = `nvidia-smi --query-gpu=temperature.gpu \
           --format=csv,noheader,nounits 2>/dev/null`.strip
    return nil if csv.empty?

    temps = csv.lines.map { |l| l.strip.to_f }
    (temps.sum / temps.size).round(PRECISION)
  end

  # Sum of fam15h_power power1_input across sockets, in watts (nil if absent).
  # (AMD "Application Power Management" running-average processor power.)
  def self.fam15h_power_watts
    inputs = Dir.glob("/sys/class/hwmon/hwmon*").select { |h|
      (File.read(File.join(h, "name")).strip == "fam15h_power" rescue false)
    }.filter_map { |h| (File.read(File.join(h, "power1_input")).strip.to_f rescue nil) }
    return nil if inputs.empty?
    (inputs.sum / 1_000_000.0)   # µW → W
  end

  # CPU temperature °C: max across k10temp instances (AMD), read from hwmon.
  def self.cpu_temp
    temps = Dir.glob("/sys/class/hwmon/hwmon*").select { |h|
      (File.read(File.join(h, "name")).strip == "k10temp" rescue false)
    }.filter_map { |h| (File.read(File.join(h, "temp1_input")).strip.to_f / 1000.0 rescue nil) }
    return nil if temps.empty?
    temps.max.round(PRECISION)
  end

  def self.per_user_gpu
    return {} unless command_exists?("nvidia-smi")

    result = Hash.new(0.0)
    raw = `nvidia-smi pmon -c 1 -s u 2>/dev/null`
    raw.each_line do |line|
      next if line.start_with?("#") || line.strip.empty?
      fields = line.split
      pid = fields[1]
      sm  = fields[3]
      next if pid == "-" || sm == "-"

      user = pid_owner(pid)
      next if user.nil? || IGNORED_USERS.include?(user)

      result[user] += sm.to_f
    end

    result.transform_values { |v| v.round(PRECISION) }
  end

  def self.command_exists?(cmd)
    ENV["PATH"].split(":").any? { |p| File.executable?(File.join(p, cmd)) }
  end

  def self.pid_owner(pid)
    File.read("/proc/#{pid}/status")[/^Uid:\s+(\d+)/, 1]
      .then { |uid| uid && Etc.getpwuid(uid.to_i).name rescue nil }
  end
end

# ─── Idle baseline (self-calibrating) ─────────────────────────────────────────
# We don't hardcode idle wattage. fam15h_power gives real CPU-package power, so
# the idle baseline is simply the LOWEST package power ever observed (the true
# floor when nothing is running). It's persisted and auto-updates downward, so it
# converges to the correct value with no hand-tuning. Seeded from a measurement
# taken 2026-07-17 (~84 W, dual Opteron 6274). Per-user power is attributed only
# ABOVE this floor, so idle draw isn't blamed on anyone.
module IdleBaseline
  STATE = File.join(__dir__, ".cpu_idle_watts")
  SEED  = 84.0
  FLOOR = 30.0   # sanity guard: ignore implausibly low readings

  def self.read
    v = (File.read(STATE).strip.to_f rescue SEED)
    v > FLOOR ? v : SEED
  end

  # Update the running minimum with a fresh reading; return the current baseline.
  def self.update(cpu_watts)
    cur = read
    if cpu_watts && cpu_watts > FLOOR && cpu_watts < cur
      File.write(STATE, cpu_watts.round(PRECISION).to_s) rescue nil
      return cpu_watts
    end
    cur
  end
end

# ─── Scratch file (JSONL) ────────────────────────────────────────────────────

module Scratch
  def self.append(path, data)
    File.open(path, "a") { |f| f.puts(JSON.generate(data)) }
  end

  def self.read_all(path)
    return [] unless File.exist?(path)

    lines = File.readlines(path)
    lines.filter_map do |line|
      JSON.parse(line.strip) rescue nil
    end
  end

  def self.clear(path)
    File.write(path, "") if File.exist?(path)
  end
end

# ─── CSV logic ────────────────────────────────────────────────────────────────

module CsvStore
  FIXED_COLUMNS = %w[
    timestamp
    samples
    cpu_percent
    gpu_percent
    gpu_mem_percent
    cpu_watts
    gpu_watts
    total_watts
    cpu_temp_c
    gpu_temp_c
  ].freeze

  def self.user_cpu_col(user)  = "cpu_user:#{user}"
  def self.user_gpu_col(user)  = "gpu_user:#{user}"
  def self.user_watts_col(user) = "watts_user:#{user}"

  def self.read_headers(path)
    return FIXED_COLUMNS.dup unless File.exist?(path)

    first_line = File.open(path, &:readline).strip
    CSV.parse_line(first_line)
  rescue EOFError, CSV::MalformedCSVError
    FIXED_COLUMNS.dup
  end

  def self.append(path, row_hash)
    headers = read_headers(path)

    new_cols = row_hash.keys - headers
    unless new_cols.empty?
      rewrite_with_new_columns(path, headers, new_cols)
      headers.concat(new_cols)
    end

    row = headers.map { |h| row_hash.fetch(h, "") }

    write_header = !File.exist?(path) || File.zero?(path)
    CSV.open(path, "a") do |csv|
      csv << headers if write_header
      csv << row
    end
  end

  def self.rewrite_with_new_columns(path, old_headers, new_cols)
    return unless File.exist?(path) && !File.zero?(path)

    all_headers = old_headers + new_cols
    rows = CSV.read(path, headers: true).map do |r|
      all_headers.map { |h| r[h] || "" }
    end

    CSV.open(path, "w") do |csv|
      csv << all_headers
      rows.each { |r| csv << r }
    end
  end
end

# ─── Summarizer ───────────────────────────────────────────────────────────────

module Summarize
  # Takes an array of sample hashes, returns a single averaged row hash.
  def self.average(samples)
    n = samples.size
    return nil if n.zero?

    # Use the hour of the first sample as the timestamp.
    first_ts = samples.first["ts"]
    hour_ts  = first_ts.sub(/:\d{2}:\d{2}$/, ":00:00")

    avg = ->(key) {
      vals = samples.map { |s| s[key] }.compact
      return "" if vals.empty?
      (vals.sum / vals.size).round(PRECISION)
    }

    # Collect all users across all samples.
    all_users = samples.flat_map { |s|
      (s["user_cpu"]&.keys || []) + (s["user_gpu"]&.keys || []) + (s["user_watts"]&.keys || [])
    }.uniq.sort

    row = {
      "timestamp"       => hour_ts,
      "samples"         => n,
      "cpu_percent"     => avg.call("cpu"),
      "gpu_percent"     => avg.call("gpu"),
      "gpu_mem_percent" => avg.call("gpu_mem"),
      "cpu_watts"       => avg.call("cpu_watts"),
      "gpu_watts"       => avg.call("gpu_watts"),
      "total_watts"     => avg.call("total_watts"),
      "cpu_temp_c"      => avg.call("cpu_temp"),
      "gpu_temp_c"      => avg.call("gpu_temp"),
    }

    all_users.each do |u|
      cpu_vals   = samples.filter_map { |s| s.dig("user_cpu", u) }
      gpu_vals   = samples.filter_map { |s| s.dig("user_gpu", u) }
      watts_vals = samples.filter_map { |s| s.dig("user_watts", u) }

      # Average over ALL samples (treat absent = 0).
      row[CsvStore.user_cpu_col(u)]   = cpu_vals.empty?   ? 0.0 : (cpu_vals.sum / n).round(PRECISION)
      row[CsvStore.user_gpu_col(u)]   = gpu_vals.empty?   ? 0.0 : (gpu_vals.sum / n).round(PRECISION)
      row[CsvStore.user_watts_col(u)] = watts_vals.empty? ? 0.0 : (watts_vals.sum / n).round(PRECISION)
    end

    row
  end
end

# ─── Report configuration ────────────────────────────────────────────────────

REPORT_HTML     = File.join(__dir__, "ganesha_usage.html")
REPORT_SCP_DEST = "simon@santafe.santafe.edu:html/"
HOSTNAME        = `hostname -s 2>/dev/null`.strip

# ─── Report generator ────────────────────────────────────────────────────────

module Report
  # Reads the CSV, computes total kWh per user (above idle), generates HTML,
  # writes it locally, and scp's it to the remote server.
  def self.generate(csv_path, html_path, scp_dest)
    unless File.exist?(csv_path)
      $stderr.puts "No CSV found at #{csv_path}"
      exit 1
    end

    rows = CSV.read(csv_path, headers: true)

    # Find all watts_user columns.
    watts_cols = rows.headers.select { |h| h&.start_with?("watts_user:") }

    # Sum watt-hours per user (each row = 1 hour, so watts value = watt-hours).
    user_wh = {}
    watts_cols.each do |col|
      user = col.sub("watts_user:", "")
      wh = rows.sum { |r| v = r[col]; (v.nil? || v.empty?) ? 0.0 : v.to_f }
      user_wh[user] = wh
    end

    # Filter to users above 1 Wh, sort descending.
    user_wh = user_wh.select { |_, wh| wh >= 1.0 }
                      .sort_by { |_, wh| -wh }

    # Compute system totals.
idle_floor = IdleBaseline.read
total_system_wh = rows.sum { |r|
  v = r["total_watts"]
  raw = (v.nil? || v.empty?) ? 0.0 : v.to_f
  [raw - idle_floor, 0.0].max
}
first_ts = rows.first["timestamp"] rescue "?"
    last_ts  = rows.to_a.last["timestamp"] rescue "?"
    num_rows = rows.size

    # Max temperatures with timestamps.
    max_cpu_row = rows.max_by { |r| v = r["cpu_temp_c"]; (v.nil? || v.empty?) ? -1 : v.to_f }
    max_gpu_row = rows.max_by { |r| v = r["gpu_temp_c"]; (v.nil? || v.empty?) ? -1 : v.to_f }
    max_cpu_temp = max_cpu_row ? (v = max_cpu_row["cpu_temp_c"]; (v.nil? || v.empty?) ? nil : v.to_f) : nil
    max_gpu_temp = max_gpu_row ? (v = max_gpu_row["gpu_temp_c"]; (v.nil? || v.empty?) ? nil : v.to_f) : nil
    max_cpu_ts   = max_cpu_row ? max_cpu_row["timestamp"] : nil
    max_gpu_ts   = max_gpu_row ? max_gpu_row["timestamp"] : nil

    # Format "2026-04-02 13:00:00" → "2 April 2026, 1 pm"
    friendly_ts = ->(ts) {
      return nil unless ts
      t = Time.parse(ts)
      hour = t.strftime("%l %p").strip.downcase  # "1 pm", "12 am"
      "%-d %B %Y, #{hour}" % { d: t.day } rescue ts
      "#{t.day} #{t.strftime('%B %Y')}, #{hour}"
    }

    # Generate HTML.
    now = Time.now.strftime("%Y-%m-%d %H:%M:%S")
    html = <<~HTML
      <!DOCTYPE html>
      <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>#{HOSTNAME} – Energy Usage</title>
        <style>
          body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
                 max-width: 600px; margin: 40px auto; padding: 0 20px;
                 color: #333; background: #fafafa; }
          h1 { font-size: 1.4em; margin-bottom: 0.2em; }
          .meta { color: #888; font-size: 0.85em; margin-bottom: 1.5em; }
          table { width: 100%; border-collapse: collapse; }
          th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #e0e0e0; }
          th { color: #666; font-weight: 600; font-size: 0.9em; }
          td.val { text-align: right; font-variant-numeric: tabular-nums; }
          tr:last-child td { border-bottom: 2px solid #ccc; }
          .total { font-weight: 600; color: #555; }
          .footer { color: #aaa; font-size: 0.75em; margin-top: 2em; }
        </style>
      </head>
      <body>
        <h1>#{HOSTNAME} energy usage</h1>
        <p class="meta">
          #{first_ts} &ndash; #{last_ts}<br>
          #{num_rows} hourly samples &middot; updated #{now}
        </p>
        <table>
          <tr><th>User</th><th style="text-align:right">kWh</th><th style="text-align:right">kg TNT equiv.</th></tr>
    HTML

    user_wh.each do |user, wh|
      kwh = wh / 1000.0
      tnt = kwh * 3.6 / 4.184   # 1 kWh = 3.6 MJ; 1 kg TNT = 4.184 MJ
      html << "      <tr><td>#{user}</td><td class=\"val\">#{"%.4f" % kwh}</td><td class=\"val\">#{"%.4f" % tnt}</td></tr>\n"
    end

    sys_kwh = total_system_wh / 1000.0
    html << <<~HTML
          <tr class="total"><td>System total</td><td class="val">#{"%.4f" % sys_kwh}</td><td class="val">#{"%.4f" % (sys_kwh * 3.6 / 4.184)}</td></tr>
        </table>
        <p class="footer">
          CPU-package power via AMD fam15h_power (APM); above-idle only, idle floor #{"%.1f" % idle_floor} W (self-calibrating minimum).
          Max average hourly temperature &mdash; CPU: #{max_cpu_temp ? "%.1f" % (max_cpu_temp * 9.0/5 + 32) : "N/A"} &deg;F#{max_cpu_ts ? " (#{friendly_ts.call(max_cpu_ts)})" : ""}.
          Users shown only if they have &ge; 1 Wh usage.
        </p>
      </body>
      </html>
    HTML

    File.write(html_path, html)
    puts "[#{now}] Report written to #{html_path}"

    # Upload via scp.
    result = system("scp -q #{html_path} #{scp_dest} 2>&1")
    if result
      puts "[#{now}] Uploaded to #{scp_dest}"
    else
      $stderr.puts "[#{now}] scp upload failed"
    end
  end
end

options = {
  csv:       DEFAULT_CSV,
  scratch:   DEFAULT_SCRATCH,
  mode:      nil,
}

OptionParser.new do |o|
  o.banner = "Usage: #{$PROGRAM_NAME} --sample | --summarize | --report | --dry-run [options]"
  o.on("--sample",       "Take a snapshot and append to scratch file")  { options[:mode] = :sample }
  o.on("--summarize",    "Average scratch samples, write to CSV")       { options[:mode] = :summarize }
  o.on("--report",       "Generate HTML report and scp to remote")      { options[:mode] = :report }
  o.on("--dry-run",      "Print a single snapshot without writing")     { options[:mode] = :dry_run }
  o.on("--csv PATH",     "CSV output path (default: #{DEFAULT_CSV})")   { |v| options[:csv] = v }
  o.on("--scratch PATH", "Scratch file path")                           { |v| options[:scratch] = v }
end.parse!

if options[:mode].nil?
  $stderr.puts "Error: specify --sample, --summarize, --report, or --dry-run"
  exit 1
end

case options[:mode]

when :sample
  cpu_total, user_cpu, cpu_watts = Measure.cpu_snapshot
  gpu_total, gpu_mem  = Measure.total_gpu
  gpu_watts           = Measure.gpu_power_watts
  user_gpu            = Measure.per_user_gpu
  cpu_temp            = Measure.cpu_temp
  gpu_temp            = Measure.gpu_temp

  # Allocate above-idle power per user proportionally. The idle baseline is the
  # self-calibrating observed floor (IdleBaseline); only power above it is
  # attributed to users.
  cpu_idle = IdleBaseline.update(cpu_watts)
  cpu_above_idle = cpu_watts ? [cpu_watts - cpu_idle, 0.0].max : 0.0
  gpu_above_idle = gpu_watts ? [gpu_watts - GPU_IDLE_WATTS, 0.0].max : 0.0

  user_watts = {}
  total_user_cpu = user_cpu.values.sum
  total_user_gpu = user_gpu.values.sum
  all_users = (user_cpu.keys + user_gpu.keys).uniq

  all_users.each do |u|
    w = 0.0
    if cpu_above_idle > 0 && total_user_cpu > 0
      w += (user_cpu.fetch(u, 0.0) / total_user_cpu) * cpu_above_idle
    end
    if gpu_above_idle > 0 && total_user_gpu > 0
      w += (user_gpu.fetch(u, 0.0) / total_user_gpu) * gpu_above_idle
    end
    user_watts[u] = w.round(PRECISION)
  end

  total_watts = [(cpu_watts || 0), (gpu_watts || 0)].sum.round(PRECISION)

  data = {
    "ts"          => Time.now.strftime("%Y-%m-%d %H:%M:%S"),
    "cpu"         => cpu_total,
    "gpu"         => gpu_total,
    "gpu_mem"     => gpu_mem,
    "cpu_watts"   => cpu_watts,
    "gpu_watts"   => gpu_watts,
    "total_watts" => total_watts,
    "cpu_temp"    => cpu_temp,
    "gpu_temp"    => gpu_temp,
    "user_cpu"    => user_cpu,
    "user_gpu"    => user_gpu,
    "user_watts"  => user_watts,
  }

  Scratch.append(options[:scratch], data)
  puts "[#{data['ts']}] Sample recorded (#{options[:scratch]})"

when :summarize
  samples = Scratch.read_all(options[:scratch])

  if samples.empty?
    $stderr.puts "[#{Time.now}] No samples to summarize."
    exit 0
  end

  row = Summarize.average(samples)
  CsvStore.append(options[:csv], row)
  Scratch.clear(options[:scratch])

  puts "[#{row['timestamp']}] Summarized #{samples.size} samples → #{options[:csv]}"

  # Update the HTML report and upload it.
  Report.generate(options[:csv], REPORT_HTML, REPORT_SCP_DEST)

when :report
  Report.generate(options[:csv], REPORT_HTML, REPORT_SCP_DEST)

when :dry_run
  cpu_total, user_cpu, cpu_watts = Measure.cpu_snapshot
  gpu_total, gpu_mem  = Measure.total_gpu
  gpu_watts           = Measure.gpu_power_watts
  user_gpu            = Measure.per_user_gpu
  cpu_temp            = Measure.cpu_temp
  gpu_temp            = Measure.gpu_temp
  all_users = (user_cpu.keys + user_gpu.keys).uniq.sort
  ts = Time.now.strftime("%Y-%m-%d %H:%M:%S")

  cpu_idle = IdleBaseline.read
  cpu_above_idle = cpu_watts ? [cpu_watts - cpu_idle, 0.0].max : 0.0
  gpu_above_idle = gpu_watts ? [gpu_watts - GPU_IDLE_WATTS, 0.0].max : 0.0
  total_user_cpu = user_cpu.values.sum
  total_user_gpu = user_gpu.values.sum

  puts "── Dry-run snapshot (#{ts}) ──"
  puts "  CPU total  : #{cpu_total}%"
  puts "  GPU total  : #{gpu_total.nil? ? 'N/A' : "#{gpu_total}%"}"
  puts "  GPU mem    : #{gpu_mem.nil?   ? 'N/A' : "#{gpu_mem}%"}"
  puts "  CPU power  : #{cpu_watts.nil? ? 'N/A' : "#{cpu_watts} W"} (#{cpu_above_idle.round(2)} W above #{cpu_idle} W idle floor)"
  puts "  GPU power  : #{gpu_watts.nil? ? 'N/A' : "#{gpu_watts} W"} (#{gpu_above_idle.round(2)} W above #{GPU_IDLE_WATTS} W idle)"
  puts "  Total      : #{[(cpu_watts || 0), (gpu_watts || 0)].sum.round(PRECISION)} W"
  puts "  CPU temp   : #{cpu_temp.nil?  ? 'N/A' : "#{cpu_temp} °C"}"
  puts "  GPU temp   : #{gpu_temp.nil?  ? 'N/A' : "#{gpu_temp} °C"}"
  puts ""
  all_users.each do |u|
    w = 0.0
    w += (user_cpu.fetch(u, 0.0) / total_user_cpu) * cpu_above_idle if cpu_above_idle > 0 && total_user_cpu > 0
    w += (user_gpu.fetch(u, 0.0) / total_user_gpu) * gpu_above_idle if gpu_above_idle > 0 && total_user_gpu > 0
    puts "  %-24s  CPU: %8.2f%%   GPU: %6.1f%%   Power: %8.4f W" % [u, user_cpu.fetch(u, 0), user_gpu.fetch(u, 0), w]
  end
end
