import math
import std

# UnitType
UNIT_SQUARE = 1
UNIT_LOGARITHMIC = 2
UNIT_LINEAR = 0

TOLERANCE_ABSOLUTE = 0
TOLERANCE_RELATIVE = 1

class PyFeedbackWorker:
    

    def __init__(self, worker, unit):
        self.worker = worker
        self.unit = unit
        self.unit_type = PyFeedbackWorker.detect_unit_type(unit)
        self.values_list = []

    # in case of write and return value is what we read
    def write(self, value):
        ret_value = self.worker(value)
        if ret_value is not None:
            self.values_list.append(value)
        return ret_value

    # in case of read
    def read(self):
        value = self.worker()
        if value is not None:
            self.values_list.append(value)
        return value

    def reset(self):
        if hasattr(self.values_list,'clear'):
            self.values_list.clear()

    def is_empty(self):
        return len(self.values_list) == 0

    def last_value(self):
        if len(self.values_list) == 0:
            self.read()
        return self.values_list[-1]

    @staticmethod
    def detect_unit_type(unit):
        if "DB" in unit.upper():
            unit_type = UNIT_LOGARITHMIC
        elif unit.upper() == "W":
            unit_type = UNIT_SQUARE
        else:
            unit_type = UNIT_LINEAR
        return unit_type


class PyFeedback:

    def __init__(self, generator=None, gen_unit="", meter=None, met_unit=""):
        self.safe_start_value = -35  # default safe start value
        self.method = 0  # this value is calculated
        self.conservative_factor = 0.5 #0..1
        self.maximum_steps = 16  # maximum steps default value
        self.maximum_step_size = None  # 0 - it means is off
        self.tolerance = 1  # default 1%
        self.tolerance_unit = TOLERANCE_RELATIVE  # tolerance set in percents
        self.set_generator(generator, gen_unit)
        self.set_meter(meter, met_unit)
        self.step_counter = 0

    def set_generator(self, generator, unit):
        self.generator_worker = PyFeedbackWorker(generator, unit)

    def set_meter(self, meter, unit):
        self.meter_worker = PyFeedbackWorker(meter, unit)

    def set_limiting_meter(self, limiting_meter, limit, tolerance, unit, method):
        self.limiting_meter_worker = PyFeedbackWorker(limiting_meter, unit)
        self.limiting_meter_method = method
        self.limiting_meter_limit = limit
        self.limiting_meter_tolerance = tolerance
        self.limiting_meter_tolerance_unit = unit

    @staticmethod
    def compute_tolerance(tolerance_method, tolerance, target_value):
        if tolerance_method == TOLERANCE_RELATIVE:
            return abs(float(target_value)) * tolerance/100.0
        return  tolerance

    def reset(self):
        self.generator_worker.reset()
        self.meter_worker.reset()
        # if we have limiting meter worker
        if hasattr(self,'limiting_meter_worker'):
            self.limiting_meter_worker.reset()

    def setup_valid(self):#TODO check setup
        if hasattr(self, 'generator_worker') and hasattr(self, 'meter_worker'):
            return True
        return False

    def set_regulated_value(self, value):
        # check method
        if self.setup_valid():
            self.method = self.detect_method(self.generator_worker.unit_type, self.meter_worker.unit_type)
            self.reset()
            return self.algorithm_1p(value)
        return False

    def set_safestart_value(self):
        self.generator_worker.write(self.safe_start_value)

    @staticmethod
    def detect_method(gen_unit_type, met_unit_type):
        method_table = [[0, 2, 6], [3, 0, 4], [7, 5, 1]]
        return method_table[gen_unit_type][met_unit_type]

    def limiting_meter_enabled(self):
        return hasattr(self,'limiting_meter_worker')

    # Feedback algorithm: 1-point linear prediction.
    def algorithm_1p(self, target_value):
        self.step_counter = 0
        found = False

        # calculate tolerance value
        tolerance_value = self.compute_tolerance(self.tolerance, self.tolerance_unit, target_value)
        # calculate limiting meter value
        if self.limiting_meter_enabled():
            limiting_tolerance_value = self.compute_tolerance(self.limiting_tolerance,
                                                              self.limiting_meter_tolerance_unit,
                                                              self.set_limiting_meter_limit)
        if self.generator_worker.is_empty():
            self.generator_worker.write(self.safe_start_value)

        # [START searching]
        for self.step_counter in range(0, self.maximum_steps):            
            # reads from script function meter or Device
            met_value = self.meter_worker.read()
            if met_value is None:
                raise Exception("Error reading meter")

            # if limiting meter fundion is enabled
            if self.limiting_meter_enabled():
                limmet_value = self.limiting_meter_worker.read()
                if limmet_value is None:
                    raise Exception("Error reading limiting meter")

            # Loop Conditions -
            # Feedback regulation condition
            found = (abs(target_value - met_value) < tolerance_value)
            if found :break

                # The limiting meter regulation condition.
            if self.limiting_meter_enabled():
                if limmet_value > self.limiting_meter_limit:  # main limiting condition
                    found = False
                if not found:
                    # feedback not in tolerance
                    if met_value < target_value:
                        found = ((self.limiting_meter_limit - limmet_value) < limiting_tolerance_value)

            # Compute the estimated target value.
            gen_estimated_value = self.estimate_next_value(self.method, self.generator_worker.last_value(), self.meter_worker.last_value(), target_value);
            gen_delta = gen_estimated_value - self.generator_worker.last_value();

            if self.limiting_meter_enabled():
                gen_estimated_limiting = self.estimate_next_value(self.limiting_meter_method, self.generator_worker.last_value(), self.meter_worker.last_value(), self.limiting_meter_limit)
                gen_inc_limiting = gen_estimated_limiting - self.generator_worker.last_value()
                # Determine which increase is lower, and take it.
                # NOTE: This works for both negative and positive increases.
                if gen_inc_limiting < gen_delta:
                    gen_delta = gen_inc_limiting

            # Apply the conservative factor.
            gen_delta = gen_delta * (1 - self.conservative_factor / (self.step_counter + 1))

            # Cut the increase if it exceeds the maximum iteration step.
            if self.maximum_step_size is not None:
                if gen_delta > self.maximum_step_size:
                    gen_delta = self.maximum_step_size
                elif gen_delta < -self.maximum_step_size:
                    gen_delta = -self.maximum_step_size

            gen_estimated_value = self.generator_worker.last_value() + gen_delta
            # write next value to generator
            if not self.generator_worker.write(gen_estimated_value):
                raise Exception("Error writing generator")
            std.sleep(10);

        return found

    @staticmethod
    def estimate_next_value(linearization_method, gen_value, met_value, target_value):
        # Estimates generator value to get to Target value from provided Generated and Measured values, using specified linerization method.
        # Algorithm: 1-point linear prediction.
        if linearization_method == 0:  # M = a*G
            met_value = 1 if met_value ==0 else met_value
            scale_factor = (met_value / gen_value)
            return target_value / scale_factor
        elif linearization_method == 1:  # M = G + q
            delta = target_value - met_value
            return gen_value + delta
        elif linearization_method == 2:  # M = a-G^2
            scale_factor = met_value / (gen_value * gen_value)
            return math.qsqrt(target_value / scale_factor)
        elif linearization_method == 3:  # M^2 = a*G
            scale_factor = met_value * met_value / gen_value
            return (target_value * target_value) / scale_factor
        elif linearization_method == 4:  # M = 10 log G + q
            delta = target_value - met_value
            return math.exp((10 * log10(gen_value) + delta) / 10)
        elif linearization_method == 5:  # 10 log M = G + q
            delta = 10 * math.log10(target_value) - 10 * math.log10(met_value)
            return gen_value + delta
        elif linearization_method == 6:  # M = 20 log G + q
            delta = target_value - met_value
            return math.exp((20 * math.log10(gen_value) + delta) / 20)
        if linearization_methodd == 7:  # 20 log M = G + q
            delta = 20 * math.log10(target_value) - 20 * math.log10(met_value)
            return  gen_value + delta

        return None
