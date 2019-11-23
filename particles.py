"""
    Models the trajectories of particles that interact with one another
    through gravitational forces, Coulomb interactions, and collisions.
"""

from matplotlib.animation import FuncAnimation, writers
import matplotlib.animation as animation
from matplotlib.patches import Circle
import matplotlib.pyplot as plt
from time import time
import numpy as np
import datetime
import os

try:
    import vpython as vp
    vpython_imported = True
except ImportError:
    vpython_imported = False
    warning_msg = ("\033[01mWARNING\033[m: Module \033[03mvpython\033[m is not"
                   " installed on this system. \033[03mvpython\033[m is "
                   "required to enable 3-D animation.")
    print(warning_msg)

try:
    import cupy as cp
    cupy_imported = True
except ImportError:
    cupy_imported = False
    warning_msg = ("\033[01mWARNING\033[m: Module \033[03mcupy\033[m is not "
                   "installed on this system. \033[03mcupy\033[m enables GPU "
                   "based acceleration through multiprocessing.")
    print(warning_msg)

np.random.seed(69420666)

def load(dirname):

    t = np.load(f"{dirname}/arr/t.npy")
    x = np.load(f"{dirname}/arr/x.npy")
    v = np.load(f"{dirname}/arr/v.npy")
    m = np.load(f"{dirname}/arr/m.npy")
    q = np.load(f"{dirname}/arr/q.npy")
    r = np.load(f"{dirname}/arr/r.npy")

    I = Simulation(x[0], v[0], m, q, r)
    I.t, I.x, I.v = t, x, v

    with open(f"{dirname}/metadata.dat") as infile:
        data = infile.read().split(" ")
        I.dt = float(data[0].split("=")[1])
        I.T = float(data[1].split("=")[1])
        I.GPU = bool(data[2].split("=")[1])
        I.collision = bool(data[3].split("=")[1])

    return I

def lattice(shape, mass, absolute_charge, distance, radius):
    shape = np.array(shape)
    p = shape.ndim
    N = np.prod(shape)

    # Setting lattice positions
    ctr_dist = 2*radius + distance
    arrays = []
    for i in shape:
        if i == 1:
            extension = np.array([0])
        else:
            extension = np.arange(0, ctr_dist*i, ctr_dist)
        arrays.append(extension)

    x0 = np.meshgrid(*arrays)
    for n,i in enumerate(x0):
        x0[n] = np.reshape(i, N)

    x0 = np.array(x0).T

    # Setting lattice velocities (zero)
    v0 = np.zeros_like(x0)
    # Setting lattice masses
    m = np.ones(N)*mass

    # Setting lattice charges
    bool_shape = ((shape + 1) % 2).astype(bool)
    slices = [slice(i) for i in shape] if shape.ndim > 0 else slice(shape)
    shape[bool_shape] += 1
    q = np.ones(np.prod(shape))
    q[::2] = -1
    q = q.reshape(shape)
    q = q[tuple(slices)]*absolute_charge
    q = q.reshape(N)

    # Setting lattice radii
    r = np.ones(N)*radius

    return Simulation(x0, v0, m, q, r)

def rand(N, p = 2, x = (0,100), v = (0,100), m = (1E7,1E5), q = (0,1E-5), r = (1,0.1)):
    N = int(N)
    x = np.random.normal(x[0], x[1], (N,p))
    v = np.random.normal(v[0], v[1], (N,p))
    m = np.random.normal(m[0], m[1], N)
    m[m < 0] = np.abs(m[m < 0])
    m[m == 0] = 1
    q = np.random.normal(q[0], q[1], N)
    r = np.random.normal(r[0], r[1], N)
    r[r < 0] = np.abs(r[r < 0])
    r[r == 0] = 1
    return Simulation(x, v, m, q, r)

class Counter:

    def __init__(self, tot_iter):
        self.counter = 0
        self.t0 = time()
        self.perc = 0
        self.tot_iter = tot_iter
        self.times = np.zeros(tot_iter)
        self.dt = 0
        print(f"\tStatus\t\t\tIn Progress {0:>3d}%", end = "")

    def __call__(self):
        self.counter += 1
        new_perc = int(100*self.counter/self.tot_iter)
        self.times[self.counter-1] = time()
        if int(time() - self.t0) > self.dt and self.counter > 1:
            self.perc = new_perc
            t_avg = np.mean(np.diff(self.times[:self.counter]))
            eta = t_avg*(self.tot_iter - self.counter)
            dd = int((eta//86400))
            hh = int((eta//3600)%24)
            mm = int((eta//60)%60)
            ss = int(eta%60)
            msg = f"\r\tStatus\t\t\tIn Progress {self.perc:>3d}% – "
            if dd > 0:
                msg += f"{dd:d} day(s) + "
            msg += f"{hh:02d}:{mm:02d}:{ss:02d}"
            print(msg, end = "")
        self.dt = time() - self.t0

    def close(self):
        dt = time() - self.t0
        dd = int((dt//86400))
        hh = int(dt//3600)%24
        mm = int((dt//60)%60)
        ss = int(dt%60)
        msg = ""
        if dd > 0:
            msg += f"{dd:d} day(s) + "
        msg += f"{hh:02d}:{mm:02d}:{ss:02d}"
        print(f"\r\tStatus\t\t\tComplete – Total Time Elapsed {msg}")

class Particle:

    def __init__(self, position, velocity, mass, charge, radius):
        """
            –– INPUT ARGUMENTS ––––––––––––––––
                    position    –   numerical array of shape (p,)
                    velocity    –   numerical array of shape (p,)
                    mass        –   number
                    charge      –   number

            –– NOT YET IMPLEMENTED ––––––––––––
                    radius      –   numerical array of shape (N,)

            –– FOR S.I. UNITS –––––––––––––––––
                    position    –   meters
                    velocity    –   meters/second
                    mass        –   kilograms
                    charge      –   coulombs
                    radius      –   meters
        """
        # Initializing Position, Velocity, Mass, and Radius
        self.x = np.array(position)
        self.v = np.array(velocity)
        self.m = np.array(mass)
        self.q = np.array(charge)
        self.r = np.array(radius)

class Simulation:

    def __init__(self, positions, velocities, masses, charges, radii):
        """
            –– INPUT ARGUMENTS ––––––––––––––––
                    positions   –   numerical array of shape (N,p)
                    velocities  –   numerical array of shape (N,p)
                    masses      –   numerical array of shape (N,)
                    charges     –   numerical array of shape (N,)

            –– NOT YET IMPLEMENTED ––––––––––––
                    radii       –   numerical array of shape (N,)

            –– FOR S.I. UNITS –––––––––––––––––
                    position    –   meters
                    velocity    –   meters/second
                    mass        –   kilograms
                    charge      –   coulombs
                    radius      –   meters
        """
        # Checking that positions and velocities are of the same shape, and
        # that masses and radii are equal on axis zero to positions and
        # velocities. Also checks if positions and velocities are 2-D, and if
        # masses and radii are 1-D.

        condition1 = not np.array_equal(positions.shape,  velocities.shape)
        condition2 = positions.shape[0] != masses.shape[0]
        condition3 = positions.shape[0] != charges.shape[0]
        condition4 = positions.ndim != 2 or masses.ndim != 1
        condition5 = charges.ndim != 1

        if condition1 or condition2 or condition3 or condition4 or condition5:
            msg = ("Invalid arrays passed to Integration object.\nArguments "
            "<positions> and <velocities> must be 2-D and of shape (N,3), "
            "while arguments <masses>, <charges>, and <radii> must be 1-D and "
            "of length N.")
            raise ValueError(msg)

        # Initializing Positions, Velocities, Masses, and Radii
        self.x0 = positions
        self.v0 = velocities
        self.m = masses
        self.q = charges
        self.r = radii

        self.N = self.x0.shape[0]
        self.p = self.x0.shape[1]

        self.attribute_reset()

    def attribute_reset(self):
        # Saves instance state on run 1, resets to this state on runs 2+
        if not hasattr(self, '_dir_backup'):
            self._dir_backup = self.__dir__().copy
        else:
            for var in self.__dir__():
                if var not in self._dir_backup() and var != "_dir_backup":
                    delattr(self, var)

    def add(self, particle):
        """
            Adds a new particle to the system.  Must be a Particle() object!
        """
        # Updating the number of particles
        self.N += 1

        # Updating the initial conditions for position and velocity
        self.x0 = np.vstack([self.x0, particle.x])
        self.v0 = np.vstack([self.v0, particle.v])

        # Including the new particle's mass, charge, and radius
        self.m = np.concatenate([self.m, [particle.m]])
        self.q = np.concatenate([self.q, [particle.q]])
        self.r = np.concatenate([self.r, [particle.r]])

        # Resetting the object to its original state, including the new data
        self.attribute_reset()

    def _test_GPU(self, collision):
        """
            Runs several iterations of an algorithm similar to the one used in
            the solve() method to gauge whether or not the GPU should be used

            If the CPU performs best or cupy isn't installed, returns False
            If the GPU performs best, returns True
        """
        if not cupy_imported:
            return False

        times = []

        # An algorithm similar to that used in the solve() method
        for mod, GPU in zip([cp, np], [True, False]):
            a = mod.random.normal(0, 0.5, (self.N, 1))
            b = mod.random.normal(0, 0.5, (self.N, self.p))

            t0 = time()
            foo = mod.linalg.norm(a*b, axis = 1)[:,np.newaxis]
            for n in range(0, 10):
                c = self._arr_del(arr = a, n = 0, GPU = GPU, axis = 0)
                c = self._arr_del(arr = a, n = 0, GPU = GPU, axis = 0)
                c = self._arr_del(arr = a, n = 0, GPU = GPU, axis = 0)
                d = self._arr_del(arr = b, n = 0, GPU = GPU, axis = 0)
                c = mod.linalg.norm(d, axis = 1)[:,np.newaxis]
                e = self._a_inv_square(m1 = 1.1, m2 = c, d2 = d, dn = c,
                                       q1 = 1.1, q2 = 1.1, G = 1.1, k = 1.1,
                                       mod = mod)
                if collision:
                    a = a + self._a_collision(m1 = 1.1, m2 = c, r1 = 1.1,
                                              r2 = c, v1 = d[0], v2 = d,
                                              d2 = d, dn = c, cf = 1.1,
                                              mod = mod, dt = 1.1)
                c = 0.5*(c+1)
            times.append(time() - t0)
        return times[0] < times[1]

    def _a_inv_square(self, m1, m2, d2, dn, q1, q2, G, k, mod):
        """
            Calculates the total acceleration on a particle due to
            gravitational and Coulomb interactions, from all other particles

            m1  – mass of current particle
            m2  – masses of all other particles
            q1  – charge of current particle
            q2  – charges of all other particles
            d2  – vectors pointing from all particles, toward the current one
            dn  – distances between all particles and current particle

            G   – universal gravitational constant: 6.67430E−11
            k   – electrostatic constant: 8.9875517887E9
        """
        # Calculating gravitational acceleration
        a_g = G*m2
        # Calculating Coulomb acceleration
        a_c = k*q2*q1/m1
        return mod.sum((a_g + a_c)*d2/dn, axis = 0)

    def _a_collision(self, m1, m2, r1, r2, v1, v2, d2, dn, cf, mod, dt):
        """
            Calculates the total acceleration of a particle due to collisions
            with all the other particles

            m1  – mass of current particle
            m2  – masses of all other particles
            r1  – radius of current particle
            r2  – radii of all other particles
            v1  – velocity of current particle
            v2  – velocities of all other particles
            d2  – vectors pointing from all particles, toward the current one
            dn  – distances between all particles and current particle

            cf  – force coefficient for collision
            mod – cupy if the GPU is active, numpy otherwise
            dt  – integration time-step
        """
        # Indices of particles that are colliding with current particle
        idx = (dn <= r2+r1).flatten()
        # Find acceleration by conservation laws for elastic collisions
        a_c = v1*(m1-m2[idx])/(m1+m2[idx]) + 2*m2[idx]*v2[idx]/(m1+m2[idx])
        # Make the collision acceleration a scalar quantity
        a_c = mod.linalg.norm(a_c, axis = 1)[:,np.newaxis]
        return -cf*mod.sum(a_c*d2[idx]/dn[idx], axis = 0)/dt

    def _arr_del(self, arr, n, GPU, axis):
        """
            Deletes an element from an array, for both cupy and numpy
        """
        if n == 0:
            return arr[n+1:]
        elif n == arr.shape[0] - 1:
            return arr[:n]
        elif GPU is True:
            return cp.concatenate([arr[:n], arr[n+1:]])
        else:
            return np.delete(arr, n, axis = axis)

    def simulation_info(self):
        """
            Returns a string of information about the ongoing simulation
        """
        if self.GPU_active is True:
            GPU = "Active"
        else:
            GPU = "Inactive"

        if self.collision is True:
            col = "Active"
        else:
            col = "Inactive"

        msg = (f"\nSIMULATION INFO:\n\n\tParticles\t\t{self.N:d}\n\t"
                   f"Dimensions\t\t{self.p:d}\n\tT\t\t\t{self.T:g}\n\tdt\t\t\t"
                   f"{self.dt:g}\n\tSteps\t\t\t{self.T//self.dt:g}\n\tCUDA"
                   f"\t\t\t{GPU}\n\tCollisions\t\t{col}")
        return msg

    def solve(self, T, dt = None, collision = True, GPU = None, debug = True):
        # Auto-selecting dt if None
        if dt is None:
            dt = T/500

        # Auto-selecting cupy or numpy depending on system/simulation
        if GPU is None:
            GPU = self._test_GPU(collision)

        # If GPU is selected or overwritten, uses cupy.  Uses numpy otherwise
        if cupy_imported is True and GPU:
            mod = cp
            self.GPU_active = True
        else:
            mod = np
            self.GPU_active = False

        # Calculating number of steps to take in integration
        steps = int(T//dt)
        if steps*dt < T:
            steps += 1
        T = steps*dt

        self.T, self.dt = T, dt
        self.collision = collision

        # Prints information on the simulation taking place
        if debug:
            print(self.simulation_info(), end = "\n\n")

        # Initializing empty arrays for positions and velocities
        x = mod.zeros((steps, self.N, self.p))
        v = mod.zeros((steps, self.N, self.p))

        # Loading masses, charges, and radii from attributes
        mass = mod.array(self.m[:,mod.newaxis])
        charge = mod.array(self.q[:,mod.newaxis])
        radius = mod.array(self.r[:,mod.newaxis])

        # Inserting initial conditions
        x[0] = mod.array(self.x0)
        v[0] = mod.array(self.v0)

        # Allocating memory for temporary variables
        v_half = mod.zeros((self.N, self.p))

        # Universal gravitational constant
        G = 6.67430E-11
        # Electrostatic constant
        k = 8.9875517887E9
        # Collision force coefficient
        cf = 0.5

        # Initilize countdown timer
        if debug:
            counter = Counter(2*steps*self.N)

        # Velocity Verlet Integration
        for m in range(1, steps):
            # Loop over each particle
            for n in range(0, self.N):
                # Masses of all particles except the current one
                m2 = self._arr_del(arr = mass, n = n, GPU = GPU, axis = 0)
                # Charges of all particles except the current one
                q2 = self._arr_del(arr = charge, n = n, GPU = GPU, axis = 0)
                # Velocities of all particles except the current one
                v2 = self._arr_del(arr = v[m-1], n = n, GPU = GPU, axis = 0)
                # Radii of all particles except the current one
                r2 = self._arr_del(arr = radius, n = n, GPU = GPU, axis = 0)
                # Vectors pointing from each particle, toward the current one
                d2 = self._arr_del(arr = x[m-1], n = n, GPU = GPU, axis = 0)\
                     - x[m-1,n]
                # Distances between current particle, and all others
                dn = mod.linalg.norm(d2, axis = 1)[:,np.newaxis]
                # Sum over total gravitational and Coulomb accelerations
                a = self._a_inv_square(m1 = mass[n], m2 = m2, d2 = d2, dn = dn,
                                       q1 = charge[n], q2 = q2, G = G, k = k,
                                       mod = mod)
                if collision:
                    # Including acceleration from interparticle collisions
                    a = a + self._a_collision(m1 = mass[n], m2 = m2,
                        r1 = radius[n], r2 = r2, v1 = v[m-1,n], v2 = v2,
                        d2 = d2, dn = dn, cf = cf, mod = mod, dt = dt)

                # Verlet half-step velocity
                v_half[n] = v[m-1,n] + dt*0.5*a
                # Updating new position
                x[m,n] = x[m-1,n] + dt*v_half[n]

                # Display countdown timer
                if debug:
                    counter()

            # Loop over each particle
            for n in range(0, self.N):
                # Masses of all particles except the current one
                m2 = self._arr_del(arr = mass, n = n, GPU = GPU, axis = 0)
                # Charges of all particles except the current one
                q2 = self._arr_del(arr = charge, n = n, GPU = GPU, axis = 0)
                # Velocities of all particles except the current one
                v2 = self._arr_del(arr = v[m], n = n, GPU = GPU, axis = 0)
                # Radii of all particles except the current one
                r2 = self._arr_del(arr = radius, n = n, GPU = GPU, axis = 0)
                # Vectors pointing from each particle, toward the current one
                d2 = self._arr_del(arr = x[m] - x[m,n], n = n, GPU = GPU,
                                   axis = 0)
                # Distances between current particle, and all others
                dn = mod.linalg.norm(d2, axis = 1)[:,np.newaxis]
                # Sum over total gravitational and Coulomb accelerations
                a = self._a_inv_square(m1 = mass[n], m2 = m2, d2 = d2, dn = dn,
                                       q1 = charge[n], q2 = q2, G = G, k = k,
                                       mod = mod)
                if collision:
                    # Including acceleration from interparticle collisions
                    a = a + self._a_collision(m1 = mass[n], m2 = m2,
                        r1 = radius[n], r2 = r2, v1 = v[m,n], v2 = v2,
                        d2 = d2, dn = dn, cf = cf, mod = mod, dt = dt)

                # Updating new velocity
                v[m,n] = v_half[n] + dt*0.5*a

                # Display countdown timer
                if debug:
                    counter()

        # Display total time elapsed
        if debug:
            counter.close()

        # Converts back to numpy, if cupy is used
        if cupy_imported is True and GPU:
            x = cp.asnumpy(x)
            v = cp.asnumpy(v)

        # Saving array of time-steps
        self.t = np.linspace(0, T, steps)
        self.x = x
        self.v = v

    def animate(self, savename = None):
        if self.p == 2:
            self.animate_2D(savename)
        elif self.p == 3:
            # Warns the user if savename is not none
            msg = (f"Cannot save 3-D animations to file – unsupported feature")
            if savename is not None:
                print(msg)
            self.animate_3D()

    def animate_2D(self, savename = None):

        # Checks if the current simulation is 2-D, raises an error otherwise
        if self.p != 2:
            raise NotImplemented("Can currently only perform 2-D animation")

        # Creating figure and axes instances
        fig, ax = plt.subplots()
        # Making sure the animation aspect ratio is 1:1
        ax.set_aspect("equal")
        # Selecting a reasonable image resolution
        fig.set_size_inches(15, 9)
        # Removing all axes, lines, ticks, and labels
        plt.axis('off')
        # Making the background black
        fig.set_facecolor("k")

        # Selecting how much green will be used for each particle at each
        # time step.  RGB is used with R = 1 and B = 0.  G is varied between
        # 0 and 1 depending on the particle's speed in time

        # Calculating particle speeds
        speeds = np.linalg.norm(self.v, axis = 2)
        # Rescaling the speeds logarithmically
        speeds_scaled = np.log(speeds + np.min(speeds) + 1E-15)
        # Maximum log of shifted speed
        v_min = np.min(speeds_scaled.flatten())
        # Minimum log of shifted speed
        v_max = np.max(speeds_scaled.flatten())
        # Rescaling the speeds in range 0,1 and subtracting from 1
        colors_g = 1-((speeds_scaled - v_min)/(v_max-v_min))

        # Initializing the circles as a list and appending them to the plot
        circles = []
        for i,j,k in zip(self.x[0], self.r, colors_g[0]):
            # Creating a circle with initial position, radius, and RGB color
            circles.append(Circle(tuple(i), j, color = (1,k,0)))
            # Adding the above circle to the plot
            ax.add_artist(circles[-1])

        # Number of standard deviations to focus animation on
        devs = 3

        # Animation initialization function
        def init():
            x0, x1 = self.x[0,:,0], self.x[0,:,1]
            cond_0 = np.abs(x0 - np.mean(x0)) <= devs*np.std(x0)
            cond_1 = np.abs(x1 - np.mean(x1)) <= devs*np.std(x1)
            idx = np.logical_and(cond_0, cond_1)
            # Removing outliers
            x0 = x0[idx]
            x1 = x1[idx]
            # Calculating the limits of x, compensating for particle radius
            xlim = np.min(x0-self.r[idx]), np.max(x0+self.r[idx])
            # Calculating the limits of y, compensating for particle radius
            ylim = np.min(x1-self.r[idx]), np.max(x1+self.r[idx])
            # Choosing the largest scale from xlim or ylim
            scale = xlim if xlim[1]-xlim[0] >= ylim[1]-ylim[0] else ylim
            # Taking the difference to calculate the largest scale
            scale = scale[1] - scale[0]
            if xlim[1]-xlim[0] >= ylim[1]-ylim[0]:
                # If x has larger limits, scales y accordingly
                y_mid = (ylim[1] - ylim[0])/2 + ylim[0]
                y_max = y_mid + scale/2
                y_min = y_mid - scale/2
                ax.set_xlim(xlim[0], xlim[1])
                ax.set_ylim(y_min, y_max)
            else:
                # If y has larger limits, scales x accordingly
                x_mid = (xlim[1] - xlim[0])/2 + xlim[0]
                x_max = x_mid + scale/2
                x_min = x_mid - scale/2
                ax.set_xlim(x_min, x_max)
                ax.set_ylim(ylim[0], ylim[1])
            return

        # Animation update frame function
        def update(m):
            # Iterating through each circle, for a single step with index m
            for n,c in enumerate(circles):
                # Moving all circles to their new centers
                c.center = tuple(self.x[m,n])
                # Adjusting the green setting depending on current speed
                c.set_color((1,colors_g[m,n],0))

            x0, x1 = self.x[m,:,0], self.x[m,:,1]
            cond_0 = np.abs(x0 - np.mean(x0)) <= devs*np.std(x0)
            cond_1 = np.abs(x1 - np.mean(x1)) <= devs*np.std(x1)
            idx = np.logical_and(cond_0, cond_1)
            # Removing outliers
            x0 = x0[idx]
            x1 = x1[idx]

            # Calculating the limits of x, compensating for particle radius
            xlim = (np.min(x0-self.r[idx]), np.max(x0+self.r[idx]))
            # Calculating the limits of y, compensating for particle radius
            ylim = (np.min(x1-self.r[idx]), np.max(x1+self.r[idx]))
            # Choosing the largest scale from xlim or ylim
            scale = xlim if xlim[1]-xlim[0] >= ylim[1]-ylim[0] else ylim
            # Taking the difference to calculate the largest scale
            scale = scale[1] - scale[0]
            if xlim[1]-xlim[0] >= ylim[1]-ylim[0]:
                # If x has larger limits, scales y accordingly
                y_mid = (ylim[1] - ylim[0])/2 + ylim[0]
                y_max = y_mid + scale/2
                y_min = y_mid - scale/2
                ax.set_xlim(xlim[0], xlim[1])
                ax.set_ylim(y_min, y_max)
            else:
                # If y has larger limits, scales x accordingly
                x_mid = (xlim[1] - xlim[0])/2 + xlim[0]
                x_max = x_mid + scale/2
                x_min = x_mid - scale/2
                ax.set_xlim(x_min, x_max)
                ax.set_ylim(ylim[0], ylim[1])
            return

        # The frame indices for each integration step
        frames = np.arange(0, self.x.shape[0], 1)

        # Initializing the animator
        anim = FuncAnimation(fig, update, frames = frames, init_func = init,
                            blit = False, interval = 25)

        if savename is None:
            # Display the animation in an interactive session
            plt.show()
        else:
            # Create a folder in which to save files
            if not os.path.isdir("animations"):
                os.mkdir("animations")
            # Setting the video background to black
            savefig_kwargs = {'facecolor':fig.get_facecolor(),
                              'repeat':True}
            # Video metadata
            metadata = {"title":"Particle Simulation",
                        "artist":"Gabriel S Cabrera",
                        "copyright":"GNU General Public License v3.0",
                        "comment":f"Number of particles: {self.N:d}"}

            # Save the animation to file using ffmpeg
            Writer = writers['ffmpeg']
            writer = Writer(fps = 60, metadata = metadata, bitrate = 2500)
            anim.save(f"animations/{savename}.mp4", writer = writer,
                      savefig_kwargs = savefig_kwargs)
            plt.close()
            # Play the video
            file_path = f"{os.getcwd()}/animations/{savename}.mp4"
            os.system(f'xdg-open {file_path}')

    def _set_color_pos(self, n, x, color_g):
        self.spheres_temp[n].pos.x = x[0]
        self.spheres_temp[n].pos.y = x[1]
        self.spheres_temp[n].pos.z = x[2]
        self.spheres_temp[n].color.x = 1 - color_g
        self.spheres_temp[n].color.z = color_g

    def animate_3D(self, savename = None):

        # Checks if vpython was successfully imported
        if not vpython_imported:
            raise ImportError("Package 'vpython' required for 3-D animation")

        # Checks if the current simulation is 2-D, raises an error otherwise
        if self.p != 3:
            raise NotImplemented("Can only perform 3-D animation")

        # Selecting how much green will be used for each particle at each
        # time step.  RGB is used with R = 1 and B = 0.  G is varied between
        # 0 and 1 depending on the particle's speed in time

        # Calculating particle speeds
        speeds = np.linalg.norm(self.v, axis = 2)
        # Rescaling the speeds logarithmically
        speeds_scaled = np.log(speeds + np.min(speeds) + 1E-15)
        # Maximum log of shifted speed
        v_min = np.min(speeds_scaled, axis = 1)[:,np.newaxis]
        # Minimum log of shifted speed
        v_max = np.max(speeds_scaled, axis = 1)[:,np.newaxis]
        # Rescaling the speeds in range 0,1 and subtracting from 1
        idx = np.greater(np.abs(v_min - v_max), 1E-10).squeeze()
        colors_g = np.zeros_like(speeds_scaled)
        colors_g[idx] = 1-((speeds_scaled[idx] - v_min[idx])/\
                        (v_max[idx]-v_min[idx]))

        # Initializing the circles as a list and appending them to the plot
        spheres = []
        for i,j,k in zip(self.x[0], self.r, colors_g[0]):
            # Creating a circle with initial position, radius, and RGB color
            pos = vp.vector(i[0], i[1], i[2])
            rgb = vp.vector(1,k,0)
            sphere = vp.sphere(pos = pos, radius = j, color = rgb)
            spheres.append(sphere)

        while True:
            for m in range(self.x.shape[0]):
                vp.rate(32)
                # Iterating through each sphere, for a single step with index m
                for n,s in enumerate(spheres):
                    # Moving all circles to their new centers
                    s.pos = vp.vector(self.x[m,n,0],self.x[m,n,1],self.x[m,n,2])
                    # Adjusting the green setting depending on current speed
                    s.color = vp.vector(1 - colors_g[m,n], 0, colors_g[m,n])

    def save(self, dirname = "nBody_save_"):
        # Create a folder in which to save files
        if not os.path.isdir("saved"):
            os.mkdir("saved")
        dirname = f"saved/{dirname}"
        # If dirname ends in "_", will automatically number the save directory
        if dirname[-1] == "_":
            ID = 0.0
            # Iterating through files until an unused number is found
            while True:
                ID_text = f"{ID:03.0f}"
                if os.path.isdir(dirname + ID_text):
                    # If current number is used, increments to check next one
                    ID += 1
                else:
                    # If current number is available, creates save directories
                    dirname = dirname + ID_text
                    os.mkdir(dirname)
                    os.mkdir(dirname + "/arr")
                    break
        elif not os.path.isdir(dirname):
            # If unnumbered, creates save directory and array subdirectory
            os.mkdir(dirname)
            os.mkdir(dirname + "/arr")
        else:
            # If unnumbered, and directory exists, creates array subdirectory
            if not os.path.isdir(dirname + "/arr"):
                os.mkdir(dirname + "/arr")

        # Saving the instance attributes to .npy files
        np.save(f"{dirname}/arr/t", self.t)
        np.save(f"{dirname}/arr/x", self.x)
        np.save(f"{dirname}/arr/v", self.v)
        np.save(f"{dirname}/arr/m", self.m)
        np.save(f"{dirname}/arr/q", self.q)
        np.save(f"{dirname}/arr/r", self.r)

        # Saving metadata, such as time steps, simulation runtime, etc...
        with open(f"{dirname}/metadata.dat", "w+") as outfile:
            msg = (f"dt={self.dt} T={self.T} GPU={self.GPU_active} "
                   f"col={self.collision}")
            outfile.write(msg)

        # Creates a human-readable log with info on simulation parameters
        with open(f"{dirname}/log.txt", "w+") as outfile:
            outfile.write(self.simulation_info())

if __name__ == "__main__":

    msg = ("To see an example of this program, run the files in the 'samples'"
           " directory, or take a look at the README.")
    print(msg)
