import numpy as np
from matplotlib import pyplot as plt
import matplotlib.animation as animation

from scipy.interpolate import interp1d
from scipy.optimize import minimize

from cardillo import System

from cardillo.discrete import RigidBody
from cardillo.constraints import Revolute
from cardillo.forces import Force
from cardillo.actuators import Motor, PDcontroller, PIDcontroller
from cardillo.solver import Moreau, BackwardEuler, Rattle, ScipyIVP

from cardillo.math import A_IB_basic, cross3
from cardillo.math import smoothstep2

from optimal_control_pendulum import optimal_control_pendulum

if __name__ == "__main__":
    # only compute forward dynamics of unforced pendulum
    only_unforced_dynmics = False

    # desired swing-up trajectory
    desired_trajectory = ["constant", "homogeneous", "optimal_torque"][1]

    # feed forward method
    feed_forward = [False, True][
        1
    ]  # inverse dynamics for "homogeneous", no feed forward for "constant"

    # feed back controller
    feed_back = [None, "PD", "PID"][1]
    # controller gains
    kp = 50
    ki = 100
    kd = 1

    l = 1
    m = 1
    theta_S = m * (l**2) / 12
    g = 10

    phi0 = 0  # np.pi / 2
    phi_dot0 = 0

    phiN = 1 * np.pi  # target angle

    system = System()

    B_r_OC = np.array([0, -l / 2, 0])
    A_IB0 = A_IB_basic(phi0).z()
    r_OC0 = A_IB0 @ B_r_OC
    B_Omega0 = np.array([0, 0, phi_dot0])
    v_C0 = cross3(B_Omega0, r_OC0)  # I_Omega0 = B_Omega0

    q0 = RigidBody.pose2q(r_OC0, A_IB0)
    u0 = np.concatenate([v_C0, B_Omega0])
    pendulum = RigidBody(m, theta_S * np.eye(3), q0=q0, u0=u0)
    pendulum.name = "pendulum"

    joint = Revolute(
        system.origin, pendulum, axis=2, angle0=phi0, A_IJ0=A_IB_basic(-np.pi / 2).z()
    )
    joint.name = "revolute joint"

    gravity = Force(np.array([0, -g * m, 0]), pendulum)

    system.add(pendulum, gravity, joint)

    # # add moment
    tau = lambda t: 0
    motor = Motor(joint, tau)
    system.add(motor)

    system.assemble()

    ############
    # simulation
    ############

    if only_unforced_dynmics:
        t1 = 6
        dt = 1e-2
        sol = Moreau(system, t1, dt).solve()

        joint.reset()
        angle = []
        for ti, qi in zip(sol.t, sol.q):
            angle.append(joint.angle(ti, qi))

        plt.plot(sol.t, angle)
        plt.show()
        exit()

    #####################
    # trajectory planning
    #####################

    t0 = 0
    t1 = 1
    if desired_trajectory == "constant":
        dt = 1e-3
        t = np.arange(t0, t1, dt)
        phi = phiN * np.ones_like(t)
        phi_dot = np.zeros(len(t) - 1)

    elif desired_trajectory == "homogeneous":
        dt = 1e-3
        t = np.arange(t0, t1, dt)
        phi = phiN * smoothstep2(t, x_min=t0, x_max=t1)
        phi_dot = (phi[1:] - phi[:-1]) / dt

        # fig, ax = plt.subplots()
        # ax.plot(t, phi, "k")
        # ax.plot(t[1:], phi_dot, "r")
        # plt.show()
        if feed_forward:
            ####################
            # inverse kinematics
            ####################
            A_IB = np.array([A_IB_basic(phi_).z() for phi_ in phi])
            r_OC = np.array([A_IB_ @ B_r_OC for A_IB_ in A_IB])
            B_Omega = np.array([[0, 0, phi_dot_] for phi_dot_ in phi_dot])
            v_C = np.array(
                [cross3(B_Omega_, r_OC_) for B_Omega_, r_OC_ in zip(B_Omega, r_OC[1:])]
            )  # I_Omega = B_Omega!!

            q = np.array(
                [RigidBody.pose2q(r_OC_, A_IB_) for r_OC_, A_IB_ in zip(r_OC, A_IB)]
            )
            u = np.concatenate([v_C, B_Omega], axis=1)
            u_dot = (u[1:] - u[:-1]) / dt

            # fig, ax = plt.subplots()
            # ax.plot(t[2:], u_dot)
            # plt.show()

            ##################
            # inverse dynamics
            ##################
            # M @ u_dot - h = W_tau @ la_tau + W_g @ la_g
            la = np.array(
                [
                    np.linalg.pinv(
                        np.concatenate(
                            (
                                system.W_tau(t_, q_).toarray(),
                                system.W_g(t_, q_).toarray(),
                            ),
                            axis=1,
                        )
                    )
                    @ (system.M(t_, q_) @ u_dot_ - system.h(t_, q_, u_))
                    for t_, q_, u_, u_dot_ in zip(t[2:], q[2:], u[1:], u_dot)
                ]
            )
            la_tau = la[:, 0]
            la_g = la[:, 1:]

            # fig, ax = plt.subplots()
            # ax.plot(t[2:], la_tau)
            # plt.show()

            # add motor moment as feedforward
            la_tau_interp = interp1d(t[2:], la_tau, axis=0, fill_value="extrapolate")
            motor.tau = lambda t: la_tau_interp(t) if t <= 1 else 0

    elif desired_trajectory == "optimal_torque":
        # initial unknowns
        # dt = 4e-2
        dt = 2e-2
        phi_dotN = 0
        _, t, phi, tau = optimal_control_pendulum(
            l, m, t1, phiN, phi_dotN, dt, g=g, phi0=phi0, phi_dot0=phi_dot0
        )
        phi_dot = (phi[1:] - phi[:-1]) / dt

        if feed_forward:
            motor.tau = interp1d(
                t[:-2], tau, axis=0, fill_value=0.0, bounds_error=False
            )

    #####################
    # feedback controller
    #####################
    # desired/planned trajectory
    phi_interp = interp1d(t, phi, axis=0, fill_value=phiN, bounds_error=False)
    phi_dot_interp = interp1d(
        t[1:], phi_dot, axis=0, fill_value=0.0, bounds_error=False
    )
    angle_des = lambda t: np.array([phi_interp(t), phi_dot_interp(t)])

    if feed_back == "PD":
        controller = PDcontroller(joint, kp, kd, angle_des)
        system.add(controller)
    elif feed_back == "PID":
        controller = PIDcontroller(joint, kp, ki, kd, angle_des)
        system.add(controller)

    system.assemble()

    ############
    # simulation
    ############

    t1 = 6
    dt = 1e-2
    joint.reset()
    # sol = Moreau(system, t1, dt).solve()
    # sol = BackwardEuler(system, t1, dt).solve()
    # sol = Rattle(system, t1, dt).solve()
    sol = ScipyIVP(system, t1, dt).solve()

    joint.reset()
    angle = []
    for ti, qi in zip(sol.t, sol.q[:, joint.qDOF]):
        angle.append(joint.angle(ti, qi))

    fig, ax = plt.subplots()
    ax.plot(sol.t, angle)
    ax.set_xlabel("t")
    ax.set_ylabel("phi")
    plt.show()

    ###########
    # animation
    ###########
    t, q = sol.t, sol.q[:, :7]

    fig, ax = plt.subplots()
    ax.set_xlabel("x [m]")
    ax.set_ylabel("y [m]")
    width = 1.5 * l
    ax.set_xlim(-width, width)
    ax.set_ylim(-width, width)
    ax.axis("equal")

    # prepare data for animation
    frames = len(t)
    target_frames = min(len(t), 200)
    frac = int(frames / target_frames)
    animation_time = 5
    interval = animation_time * 1000 / target_frames

    frames = target_frames
    t = t[::frac]
    q = q[::frac]

    (line,) = ax.plot([], [], "-ok")
    angles = np.linspace(0, 2 * np.pi, num=100, endpoint=True)
    ax.plot(np.cos(angles), np.sin(angles), "--k")

    def update(t, q, line):
        r_OC = pendulum.r_OP(t, q)
        line.set_data([0, 2 * r_OC[0]], [0, 2 * r_OC[1]])
        return (line,)

    def animate(i):
        update(t[i], q[i], line)

    anim = animation.FuncAnimation(
        fig, animate, frames=frames, interval=interval, blit=False
    )

    plt.show()