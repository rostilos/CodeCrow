package org.rostilos.codecrow.webserver.service;

import org.rostilos.codecrow.core.dto.user.UserDTO;
import org.rostilos.codecrow.core.model.user.User;
import org.rostilos.codecrow.webserver.exception.user.UserIdNotFoundException;
import org.rostilos.codecrow.core.persistence.repository.user.UserRepository;
import org.rostilos.codecrow.webserver.dto.request.user.UpdateUserDataRequest;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.Optional;

@Service
@Transactional
public class UserService implements UserDetailsService {
    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;

    public UserService(UserRepository userRepository, PasswordEncoder passwordEncoder) {
        this.userRepository = userRepository;
        this.passwordEncoder = passwordEncoder;
    }

    @Transactional(readOnly = true)
    public User getUserById(Long userId) throws UserIdNotFoundException {
        return userRepository.findById(userId)
                .orElseThrow(() -> new UserIdNotFoundException("User Not Found with ID: " + userId));
    }

    @Transactional(readOnly = true)
    public UserDTO getUserDTOById(Long userId) throws UserIdNotFoundException {
        User user = getUserById(userId);
        return UserDTO.fromUser(user);
    }

    @Transactional(readOnly = true)
    public Optional<User> findUserById(Long userId) {
        return userRepository.findById(userId);
    }

    @Transactional(readOnly = true)
    public boolean userExistsById(Long userId) {
        return userRepository.existsById(userId);
    }

    public void updateUserInformation(Long userId, UpdateUserDataRequest updateRequest) throws UserIdNotFoundException {
        User user = getUserById(userId);

        if (updateRequest.getEmail() != null && !updateRequest.getEmail().isEmpty()) {
            validateEmailNotTaken(updateRequest.getEmail(), userId);
            user.setEmail(updateRequest.getEmail());
        }

        if (updateRequest.getUsername() != null && !updateRequest.getUsername().isEmpty()) {
            validateUsernameNotTaken(updateRequest.getUsername(), userId);
            user.setUsername(updateRequest.getUsername());
        }

        if(updateRequest.getCompany() != null && !updateRequest.getCompany().isEmpty()) {
            user.setCompany(updateRequest.getCompany());
        }

        User savedUser = userRepository.save(user);
        UserDTO.fromUser(savedUser);
    }

    public void partialUpdateUserInformation(Long userId, UpdateUserDataRequest updateRequest) throws UserIdNotFoundException {
        User user = getUserById(userId);
        boolean updated = false;

        // Only update non-null fields
        if (updateRequest.getEmail() != null && !updateRequest.getEmail().isEmpty()) {
            validateEmailNotTaken(updateRequest.getEmail(), userId);
            user.setEmail(updateRequest.getEmail());
            updated = true;
        }

        if (updateRequest.getUsername() != null && !updateRequest.getUsername().isEmpty()) {
            validateUsernameNotTaken(updateRequest.getUsername(), userId);
            user.setUsername(updateRequest.getUsername());
            updated = true;
        }

        if(updateRequest.getCompany() != null && updateRequest.getCompany().isEmpty()) {
            user.setCompany(updateRequest.getCompany());
            updated = true;
        }

        if (!updated) {
            throw new IllegalArgumentException("No fields to update provided");
        }

        User savedUser = userRepository.save(user);
        UserDTO.fromUser(savedUser);
    }

    private void validateEmailNotTaken(String email, Long userId) {
        Optional<User> existingUserOpt = userRepository.findByEmail(email);
        if (existingUserOpt.isPresent() && !existingUserOpt.get().getId().equals(userId)) {
            throw new IllegalArgumentException("Email is already taken by another user");
        }
    }

    private void validateUsernameNotTaken(String username, Long userId) {
        Optional<User> existingUser = userRepository.findByUsername(username);
        if (existingUser.isPresent() && !existingUser.get().getId().equals(userId)) {
            throw new IllegalArgumentException("Username is already taken by another user");
        }
    }

    @Transactional(readOnly = true)
    public User findByUsername(String username) {
        return userRepository.findByUsername(username)
                .orElseThrow(() -> new UserIdNotFoundException("User not found with username: " + username));
    }

    @Transactional(readOnly = true)
    public User findByEmail(String email) {
        return userRepository.findByEmail(email)
                .orElseThrow(() -> new UserIdNotFoundException("User not found with email: " + email));
    }

    @Override
    public UserDetails loadUserByUsername(String username) throws UsernameNotFoundException {
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new UsernameNotFoundException("User not found with username: " + username));
        return new org.springframework.security.core.userdetails.User(user.getUsername(), user.getPassword(), new ArrayList<>());
    }

    @Transactional
    public void changePassword(Long userId, String currentPassword, String newPassword) {
        User user = getUserById(userId);

        // Verify current password
        if (!passwordEncoder.matches(currentPassword, user.getPassword())) {
            throw new IllegalArgumentException("Current password is incorrect");
        }

        // Encode and set new password
        user.setPassword(passwordEncoder.encode(newPassword));
        userRepository.save(user);
    }
}